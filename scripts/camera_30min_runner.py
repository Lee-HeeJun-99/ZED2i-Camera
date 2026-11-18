#!/usr/bin/env python3
"""Command-free A-H ZED capture runner. Measurement starts at the first frame."""
import argparse,base64,csv,glob,io,json,os,platform,re,subprocess,threading,time,traceback
from collections import deque
from pathlib import Path

import psutil,yaml

CASE_DIRS={'A':'A_ros_idle','B':'B_ros_inference','C':'C_action_preparation','D':'D_sdk_read','E':'E_sdk_grab_retrieve','F':'F_sdk_validity_off','G':'G_opencv_v4l2','H':'H_native_v4l2'}
FRAME_FIELDS=['frame_index','receive_monotonic_s','receive_wall_s','source_timestamp_s','source_age_s','receive_gap_s','source_gap_s','sequence','sequence_delta','width','height','encoding','bytesused','v4l2_flags','v4l2_error_flag','backend_status']

def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp');tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n');os.replace(tmp,path)
def command(args,timeout=8):
    try:
        result=subprocess.run(args,text=True,capture_output=True,timeout=timeout,check=False)
        return result.stdout.strip() or result.stderr.strip()
    except Exception as e:return 'NOT_AVAILABLE: '+repr(e)
def detect_device(config):
    requested=str(config['experiment'].get('v4l2_device','auto'))
    if requested!='auto':return requested
    nodes=sorted(glob.glob('/dev/video*'))
    for node in nodes:
        text=command(['udevadm','info','--query=property','--name',node])
        if 'ZED' in text.upper():return node
    return nodes[0] if nodes else None
def environment(config,case,device):
    return dict(collected_at=time.strftime('%Y-%m-%dT%H:%M:%S%z'),case=case,hostname=platform.node(),kernel=platform.release(),python=platform.python_version(),
      physical_commands=0,command_clients_created=False,device=device or 'NOT_AVAILABLE',usb_tree=command(['lsusb','-t']),usb_devices=command(['lsusb']),
      nvidia_smi=command(['nvidia-smi','--query-gpu=name,driver_version','--format=csv,noheader']),kernel_camera_log=command(['dmesg','--color=never']),zed_diagnostic_file='/home/ubuntu/ZED_Diagnostic_Results.json' if Path('/home/ubuntu/ZED_Diagnostic_Results.json').exists() else 'NOT_AVAILABLE',
      ros_distro=os.environ.get('ROS_DISTRO','NOT_AVAILABLE'),ros_domain_id=os.environ.get('ROS_DOMAIN_ID','0'),config=config)

class ResourceSampler(threading.Thread):
    def __init__(self,path,period,stop,start_ref):super().__init__(daemon=True);self.path=path;self.period=period;self.stop=stop;self.start_ref=start_ref
    def run(self):
        fields=['relative_s','wall_s','cpu_percent','memory_percent','memory_available_bytes','zed_cpu_percent','model_cpu_percent','gpu_util_percent','gpu_memory_used_mb','gpu_temperature_c','cpu_temperature_c']
        with self.path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();psutil.cpu_percent(None)
            while not self.stop.is_set():
                start=self.start_ref[0];zed=model=0.0
                for p in psutil.process_iter(['name','cmdline']):
                    try:
                        text=' '.join(p.info.get('cmdline') or [])+' '+(p.info.get('name') or '')
                        if re.search(r'zed|camera',text,re.I):zed+=p.cpu_percent(None)
                        if re.search(r'openvla|oft|model.*server',text,re.I):model+=p.cpu_percent(None)
                    except (psutil.NoSuchProcess,psutil.AccessDenied):pass
                gpu=['','',''];raw=command(['nvidia-smi','--query-gpu=utilization.gpu,memory.used,temperature.gpu','--format=csv,noheader,nounits'],3)
                if not raw.startswith('NOT_AVAILABLE') and 'failed' not in raw.lower():gpu=(raw.splitlines()[0].split(',')+['','',''])[:3]
                temps=psutil.sensors_temperatures();cpu_temp=next((x.current for vals in temps.values() for x in vals if x.current is not None),None)
                mem=psutil.virtual_memory();w.writerow(dict(relative_s='' if start is None else time.monotonic()-start,wall_s=time.time(),cpu_percent=psutil.cpu_percent(None),memory_percent=mem.percent,memory_available_bytes=mem.available,zed_cpu_percent=zed,model_cpu_percent=model,gpu_util_percent=gpu[0].strip(),gpu_memory_used_mb=gpu[1].strip(),gpu_temperature_c=gpu[2].strip(),cpu_temperature_c=cpu_temp));f.flush()
                self.stop.wait(self.period)

class FrameWriter:
    def __init__(self,path,duration,start_ref,stop):
        self.file=path.open('w',newline='');self.writer=csv.DictWriter(self.file,fieldnames=FRAME_FIELDS);self.writer.writeheader();self.duration=duration;self.start_ref=start_ref;self.stop=stop;self.prev=None;self.count=0
    def add(self,receive,wall,source=None,sequence=None,**extra):
        if self.start_ref[0] is None:self.start_ref[0]=receive
        if receive-self.start_ref[0]>=self.duration:self.stop.set();return False
        row={k:'' for k in FRAME_FIELDS};row.update(frame_index=self.count,receive_monotonic_s=receive,receive_wall_s=wall,source_timestamp_s=source,sequence=sequence,**extra)
        # ROS/ZED SDK stamps are epoch-like. V4L2 timeval is commonly monotonic;
        # cross-domain subtraction would be meaningless and is intentionally blank.
        if source is not None and source>1_000_000_000:row['source_age_s']=wall-source
        if self.prev:
            row['receive_gap_s']=receive-self.prev[0]
            if source is not None and self.prev[1] is not None:row['source_gap_s']=source-self.prev[1]
            if sequence is not None and self.prev[2] is not None:row['sequence_delta']=sequence-self.prev[2]
        self.writer.writerow(row);self.count+=1;self.prev=(receive,source,sequence)
        if self.count%30==0:self.file.flush()
        return True
    def close(self):self.file.flush();os.fsync(self.file.fileno());self.file.close()

def check_model(url,send_prediction=False):
    from urllib.request import urlopen
    with urlopen(url+'/health',timeout=5) as r:return json.load(r)
def ros_case(case,cfg,out,fw,stop,inference_rows):
    import rclpy
    from rclpy.qos import qos_profile_sensor_data
    from sensor_msgs.msg import Image
    rclpy.init();node=rclpy.create_node('zed_camera_30min_'+case.lower());latest=[None];lock=threading.Lock();joint_samples=deque(maxlen=300);state_client=None;GetRobotState=None
    def cb(msg):
        now=time.monotonic();wall=time.time();source=msg.header.stamp.sec+msg.header.stamp.nanosec/1e9
        if fw.add(now,wall,source,width=msg.width,height=msg.height,encoding=msg.encoding,backend_status='ROS_CALLBACK'):
            if case in ('B','C'):
                with lock:latest[0]=(msg,now,source)
    sub=node.create_subscription(Image,cfg['experiment']['ros_topic'],cb,qos_profile_sensor_data)
    diagnostic_rows=[];diagnostic_sub=None
    try:
        from diagnostic_msgs.msg import DiagnosticArray
        def diagnostic(msg):
            now=time.monotonic()
            for state in msg.status:
                if 'zed' not in state.name.lower():continue
                for pair in state.values:
                    if pair.key.startswith('Data Capture') or pair.key in ('Camera Grab rate','Frame Drop rate'):
                        level=int.from_bytes(state.level,'little') if isinstance(state.level,bytes) else int(state.level)
                        diagnostic_rows.append({'receive_monotonic_s':now,'name':state.name,'level':level,'message':state.message,'key':pair.key,'value':pair.value})
        diagnostic_sub=node.create_subscription(DiagnosticArray,'/diagnostics',diagnostic,10)
    except ImportError:pass
    joint_sub=None
    if case=='C':
        try:
            from sensor_msgs.msg import JointState
            def joints(msg):joint_samples.append((time.monotonic(),list(msg.velocity)))
            joint_sub=node.create_subscription(JointState,'/dsr01/joint_states',joints,qos_profile_sensor_data)
            from dsr_msgs2.srv import GetRobotState as _GetRobotState
            GetRobotState=_GetRobotState;state_client=node.create_client(GetRobotState,'/dsr01/system/get_robot_state')
        except (ImportError,RuntimeError):pass
    worker_stop=threading.Event();inference_raw=(out/'inference.jsonl').open('x') if case in ('B','C') else None
    def infer_loop():
        from urllib.request import Request,urlopen
        from PIL import Image as PILImage
        last=None;idx=0;log=None
        if case=='C':log=(out/'action_preparation.jsonl').open('x')
        while not stop.is_set() and not worker_stop.is_set():
            with lock:item=latest[0]
            if item is None or item[1]==last:time.sleep(.005);continue
            msg,received,source=item;last=received;started=time.monotonic();row={'request_index':idx,'request_wall_s':time.time(),'inference_start_monotonic_s':started,'camera_receive_monotonic_s':received,'camera_source_timestamp_s':source,'camera_age_at_inference_start_s':started-received}
            try:
                modes={'rgb8':('RGB',3),'bgr8':('BGR',3),'rgba8':('RGBX',4),'bgra8':('BGRX',4)};raw,ch=modes[msg.encoding]
                image=PILImage.frombytes('RGB',(msg.width,msg.height),bytes(msg.data),'raw',raw,msg.step,1);buf=io.BytesIO();image.save(buf,'JPEG',quality=95);jpeg=buf.getvalue()
                payload={'image_jpeg_base64':base64.b64encode(jpeg).decode(),'instruction':cfg['experiment']['inference_instruction']}
                req=Request(cfg['experiment']['inference_url']+'/predict',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urlopen(req,timeout=5) as response:prediction=json.load(response)
                row.update(inference_end_monotonic_s=time.monotonic(),status='OK',response_keys=sorted(prediction))
                if case=='C':
                    jpg=out/f'action_{idx:06d}.jpg';png=out/f'action_{idx:06d}.png';jpg.write_bytes(jpeg);image.save(png,'PNG');prep={'index':idx,'jpeg':jpg.name,'png':png.name,'jsonl_fsync':True,'robot_state':'NOT_AVAILABLE','settle_check':'NOT_AVAILABLE','physical_commands':0}
                    recent=[v for t,v in list(joint_samples) if time.monotonic()-t<=1.0]
                    if recent:prep['settle_check']={'samples':len(recent),'all_abs_velocity_le_0_01':all(all(abs(x)<=.01 for x in v) for v in recent)}
                    if state_client is not None and state_client.service_is_ready():
                        future=state_client.call_async(GetRobotState.Request());limit=time.monotonic()+2
                        while not future.done() and time.monotonic()<limit and not stop.is_set():time.sleep(.005)
                        prep['robot_state']=getattr(future.result(),'robot_state','NOT_AVAILABLE') if future.done() and future.exception() is None else 'NOT_AVAILABLE'
                    log.write(json.dumps(prep)+'\n');log.flush();os.fsync(log.fileno());row['action_preparation']=prep
            except Exception as e:row.update(inference_end_monotonic_s=time.monotonic(),status='ERROR',error=repr(e))
            row['inference_duration_s']=row['inference_end_monotonic_s']-started;inference_rows.append(row)
            inference_raw.write(json.dumps(row,ensure_ascii=False,default=str)+'\n');inference_raw.flush();idx+=1
        if log:log.close()
    t=None
    if case in ('B','C'):t=threading.Thread(target=infer_loop,daemon=True);t.start()
    deadline=time.monotonic()+cfg['experiment']['warmup_timeout_s']
    try:
        while not stop.is_set():
            rclpy.spin_once(node,timeout_sec=.01)
            if fw.start_ref[0] is not None and time.monotonic()-fw.start_ref[0]>=fw.duration:stop.set()
            if fw.start_ref[0] is None and time.monotonic()>deadline:raise TimeoutError('no ROS image during warmup timeout')
    finally:
        worker_stop.set()
        if t:t.join(timeout=10)
        if inference_raw:inference_raw.close()
        node.destroy_subscription(sub)
        if diagnostic_sub:node.destroy_subscription(diagnostic_sub)
        if joint_sub:node.destroy_subscription(joint_sub)
        with (out/'sdk_diagnostics.csv').open('w',newline='') as f:
            fields=['receive_monotonic_s','name','level','message','key','value'];w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(diagnostic_rows)
        node.destroy_node();rclpy.shutdown()

def sdk_case(case,cfg,fw,stop):
    import pyzed.sl as sl
    init=sl.InitParameters();init.camera_resolution=sl.RESOLUTION.HD720;init.camera_fps=30;init.depth_mode=sl.DEPTH_MODE.NONE;init.enable_image_validity_check=bool(cfg['cases'][case]['validity_check'])
    cam=sl.Camera();err=cam.open(init)
    if err!=sl.ERROR_CODE.SUCCESS:raise RuntimeError('ZED open failed: '+str(err))
    mat=sl.Mat();retrieve=bool(cfg['cases'][case]['retrieve']);use_read=cfg['cases'][case]['backend']=='sdk_read'
    deadline=time.monotonic()+cfg['experiment']['warmup_timeout_s'];seq=0
    try:
        while not stop.is_set():
            if fw.start_ref[0] is not None and time.monotonic()-fw.start_ref[0]>=fw.duration:stop.set();break
            err=cam.read() if use_read else cam.grab()
            now=time.monotonic();wall=time.time()
            if err==sl.ERROR_CODE.SUCCESS:
                if retrieve:cam.retrieve_image(mat,sl.VIEW.LEFT,sl.MEM.CPU)
                source=cam.get_timestamp(sl.TIME_REFERENCE.IMAGE).get_nanoseconds()/1e9
                fw.add(now,wall,source,seq,width=1280,height=720,encoding='SDK_LEFT' if retrieve else '',backend_status=str(err));seq+=1
            elif fw.start_ref[0] is None and time.monotonic()>deadline:raise TimeoutError('no SDK frame during warmup timeout')
    finally:cam.close()

def opencv_case(cfg,device,fw,stop):
    import cv2
    cap=cv2.VideoCapture(device,cv2.CAP_V4L2);cap.set(cv2.CAP_PROP_FRAME_WIDTH,cfg['experiment']['v4l2_width']);cap.set(cv2.CAP_PROP_FRAME_HEIGHT,cfg['experiment']['v4l2_height']);cap.set(cv2.CAP_PROP_FPS,30);cap.set(cv2.CAP_PROP_FOURCC,cv2.VideoWriter_fourcc(*cfg['experiment']['v4l2_fourcc']));cap.set(cv2.CAP_PROP_CONVERT_RGB,0)
    if not cap.isOpened():raise RuntimeError('OpenCV V4L2 open failed')
    deadline=time.monotonic()+cfg['experiment']['warmup_timeout_s'];seq=0
    try:
        while not stop.is_set():
            if fw.start_ref[0] is not None and time.monotonic()-fw.start_ref[0]>=fw.duration:stop.set();break
            ok,frame=cap.read();now=time.monotonic()
            if ok:fw.add(now,time.time(),sequence=seq,width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),encoding='V4L2_RAW',bytesused=getattr(frame,'nbytes',''),backend_status='OK');seq+=1
            elif fw.start_ref[0] is None and time.monotonic()>deadline:raise TimeoutError('no OpenCV frame during warmup timeout')
    finally:cap.release()

def main():
    p=argparse.ArgumentParser();p.add_argument('--case',required=True,choices=CASE_DIRS);p.add_argument('--config',default=str(Path(__file__).parents[1]/'config/camera_30min.yaml'));p.add_argument('--output-root');p.add_argument('--test-duration',type=float,help='Testing only; marks result TEST_ONLY, never a valid 30-minute case');a=p.parse_args()
    cfg=yaml.safe_load(Path(a.config).read_text());duration=float(cfg['experiment']['duration_s']);test_only=a.test_duration is not None
    if duration!=1800:raise SystemExit('config duration_s must be exactly 1800')
    if test_only:
        if not 0<a.test_duration<=30:raise SystemExit('--test-duration must be in (0,30]');duration=a.test_duration
    root=Path(a.output_root) if a.output_root else Path(__file__).parents[1]/'camera_30min_validation';out=root/CASE_DIRS[a.case]
    if out.exists():raise SystemExit('refusing to overwrite existing output: '+str(out))
    out.mkdir(parents=True);(out/'errors.log').touch();device=detect_device(cfg)
    atomic_json(out/'config_snapshot.json',dict(case=a.case,duration_s=duration,valid_30min_result=not test_only,case_config=cfg['cases'][a.case],experiment=cfg['experiment']))
    atomic_json(out/'environment.json',environment(cfg,a.case,device));stop=threading.Event();start_ref=[None];resources=ResourceSampler(out/'resource_metrics.csv',cfg['experiment']['resource_period_s'],stop,start_ref);resources.start();fw=FrameWriter(out/'frames.csv',duration,start_ref,stop);inference_rows=[];status='COMPLETE';reason=None
    try:
        if a.case in 'ABC':atomic_json(out/'model_health.json',check_model(cfg['experiment']['inference_url']))
        if a.case in 'ABC':ros_case(a.case,cfg,out,fw,stop,inference_rows)
        elif a.case in 'DEF':sdk_case(a.case,cfg,fw,stop)
        elif a.case=='G':
            if not device:raise RuntimeError('no /dev/video device')
            opencv_case(cfg,device,fw,stop)
        else:
            if not device:raise RuntimeError('no /dev/video device')
            binary=Path(__file__).parents[1]/'build/native_v4l2_capture'
            native_path=out/'native_frames.csv';watch_done=threading.Event()
            def watch_native_start():
                while not watch_done.is_set() and start_ref[0] is None:
                    try:
                        lines=native_path.read_text().splitlines()
                        if len(lines)>1:start_ref[0]=float(lines[1].split(',')[1]);return
                    except (FileNotFoundError,ValueError,IndexError):pass
                    watch_done.wait(.1)
            watcher=threading.Thread(target=watch_native_start,daemon=True);watcher.start()
            try:subprocess.run([str(binary),device,str(cfg['experiment']['v4l2_width']),str(cfg['experiment']['v4l2_height']),'30',cfg['experiment']['v4l2_fourcc'],str(duration),str(native_path)],check=True)
            finally:watch_done.set();watcher.join(timeout=2)
            with native_path.open() as f:
                for row in csv.DictReader(f):fw.add(float(row['receive_monotonic_s']),float(row['receive_wall_s']),float(row['source_timestamp_s']),int(row['sequence']),bytesused=row['bytesused'],v4l2_flags=row['v4l2_flags'],v4l2_error_flag=row['error_flag'],encoding='V4L2_RAW',backend_status='DQBUF')
            stop.set()
    except KeyboardInterrupt:status='FAILED';reason='INTERRUPTED'
    except Exception as e:
        status='FAILED';reason=repr(e);(out/'errors.log').write_text(traceback.format_exc())
    finally:
        stop.set();resources.join(timeout=5);fw.close()
        with (out/'inference.csv').open('w',newline='') as f:
            fields=sorted({k for r in inference_rows for k in r}) or ['request_index'];w=csv.DictWriter(f,fieldnames=fields,extrasaction='ignore');w.writeheader();w.writerows(inference_rows)
        actual=0 if start_ref[0] is None else max(0,time.monotonic()-start_ref[0]);atomic_json(out/'run_status.json',dict(case=a.case,status='TEST_ONLY' if test_only and status=='COMPLETE' else status,reason=reason,requested_duration_s=duration,actual_elapsed_s=actual,frame_count=fw.count,physical_commands=0,valid_30min_result=(not test_only and status=='COMPLETE' and actual>=1800)))
    return 0 if status=='COMPLETE' else 1
if __name__=='__main__':raise SystemExit(main())
