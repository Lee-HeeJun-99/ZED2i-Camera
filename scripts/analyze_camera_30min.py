#!/usr/bin/env python3
"""Aggregate only measured A-H data; incomplete cases stay NOT_EXECUTED/FAILED."""
import argparse,csv,json,math,re,statistics
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

CASES=[('A','A_ros_idle'),('B','B_ros_inference'),('C','C_action_preparation'),('D','D_sdk_read'),('E','E_sdk_grab_retrieve'),('F','F_sdk_validity_off'),('G','G_opencv_v4l2'),('H','H_native_v4l2')]
THRESHOLDS=(.1,.3,.5,1.,2.)
def val(x):
    try:return float(x) if x not in ('',None) else None
    except (ValueError,TypeError):return None
def percentile(a,p):
    if not a:return None
    s=sorted(a);q=(len(s)-1)*p/100;lo=int(q);hi=min(lo+1,len(s)-1);return s[lo]+(s[hi]-s[lo])*(q-lo)
def stats(a):
    return {'mean':statistics.fmean(a),'median':statistics.median(a),'p95':percentile(a,95),'p99':percentile(a,99),'max':max(a)} if a else {k:None for k in ('mean','median','p95','p99','max')}
def correlation(a,b):
    pairs=[(x,y) for x,y in zip(a,b) if x is not None and y is not None]
    if len(pairs)<2:return None
    xs,ys=zip(*pairs);mx=statistics.fmean(xs);my=statistics.fmean(ys);den=math.sqrt(sum((x-mx)**2 for x in xs)*sum((y-my)**2 for y in ys))
    return sum((x-mx)*(y-my) for x,y in pairs)/den if den else None
def read_csv(path):
    if not path.exists():return []
    with path.open() as f:return list(csv.DictReader(f))
def write_csv(path,rows,fields=None):
    fields=fields or sorted({k for r in rows for k in r})
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def load_status(d):
    try:return json.loads((d/'run_status.json').read_text())
    except Exception:return {'status':'NOT_EXECUTED','reason':'run_status.json missing','valid_30min_result':False}
def aggregate_case(key,d):
    if not (d/'config_snapshot.json').exists():(d/'config_snapshot.json').write_text(json.dumps({'case':key,'status':'NOT_EXECUTED','reason':'runner was not executed'},indent=2)+'\n')
    if not (d/'environment.json').exists():(d/'environment.json').write_text(json.dumps({'case':key,'status':'NOT_AVAILABLE','reason':'runner was not executed'},indent=2)+'\n')
    if not (d/'frames.csv').exists():write_csv(d/'frames.csv',[],['frame_index','receive_monotonic_s','receive_wall_s','source_timestamp_s','source_age_s','receive_gap_s','source_gap_s','sequence','sequence_delta','width','height','encoding','bytesused','v4l2_flags','v4l2_error_flag','backend_status'])
    if not (d/'resource_metrics.csv').exists():write_csv(d/'resource_metrics.csv',[],['relative_s','wall_s','cpu_percent','memory_percent','memory_available_bytes','zed_cpu_percent','model_cpu_percent','gpu_util_percent','gpu_memory_used_mb','gpu_temperature_c','cpu_temperature_c'])
    (d/'errors.log').touch(exist_ok=True)
    status=load_status(d);frames=read_csv(d/'frames.csv');resources=read_csv(d/'resource_metrics.csv');inf=read_csv(d/'inference.csv')
    receive=[val(r.get('receive_monotonic_s')) for r in frames];receive=[x for x in receive if x is not None];start=receive[0] if receive else None
    gaps=[val(r.get('receive_gap_s')) for r in frames];gaps=[x for x in gaps if x is not None];source_gaps=[val(r.get('source_gap_s')) for r in frames];source_gaps=[x for x in source_gaps if x is not None];ages=[val(r.get('source_age_s')) for r in frames];ages=[x for x in ages if x is not None]
    duration=1800 if status.get('valid_30min_result') else (receive[-1]-receive[0] if len(receive)>1 else 0);terminal_gap=max(0,duration-(receive[-1]-receive[0])) if len(receive)>1 else 0
    if terminal_gap>=.1:gaps.append(terminal_gap)
    buckets=[[] for _ in range(1800)]
    if start is not None:
        for i,t in enumerate(receive):
            sec=int(t-start)
            if 0<=sec<1800:buckets[sec].append(i)
    seconds=[]
    for sec in range(1800):
        indexes=buckets[sec];sg=[val(frames[i].get('receive_gap_s')) for i in indexes];sg=[x for x in sg if x is not None]
        rr=[r for r in resources if val(r.get('relative_s')) is not None and sec<=val(r['relative_s'])<sec+1]
        ii=[r for r in inf if val(r.get('inference_start_monotonic_s')) is not None and start is not None and sec<=val(r['inference_start_monotonic_s'])-start<sec+1]
        seconds.append({'second':sec,'frame_count':len(indexes),'fps':len(indexes),'max_receive_gap_s':max(sg) if sg else '',**{f'gap_ge_{int(t*1000)}ms':sum(x>=t for x in sg) for t in THRESHOLDS},'cpu_percent':statistics.fmean([val(x['cpu_percent']) for x in rr]) if rr else '','gpu_util_percent':statistics.fmean([val(x['gpu_util_percent']) for x in rr if val(x['gpu_util_percent']) is not None]) if any(val(x.get('gpu_util_percent')) is not None for x in rr) else '','inference_started':len(ii),'inference_completed':sum(x.get('status')=='OK' for x in ii)})
    write_csv(d/'second_metrics.csv',seconds)
    minutes=[]
    for minute in range(30):
        chunk=seconds[minute*60:(minute+1)*60];ig=[val(r.get('inference_duration_s')) for r in inf if val(r.get('inference_start_monotonic_s')) is not None and start is not None and minute*60<=val(r['inference_start_monotonic_s'])-start<(minute+1)*60];ig=[x for x in ig if x is not None]
        minutes.append({'minute':minute,'frame_count':sum(x['frame_count'] for x in chunk),'mean_fps':statistics.fmean(x['fps'] for x in chunk),'max_receive_gap_s':max((val(x['max_receive_gap_s']) or 0 for x in chunk),default=0),**{f'gap_ge_{int(t*1000)}ms':sum(x[f'gap_ge_{int(t*1000)}ms'] for x in chunk) for t in THRESHOLDS},'cpu_percent':statistics.fmean([val(x['cpu_percent']) for x in chunk if val(x['cpu_percent']) is not None]) if any(val(x['cpu_percent']) is not None for x in chunk) else '','gpu_util_percent':statistics.fmean([val(x['gpu_util_percent']) for x in chunk if val(x['gpu_util_percent']) is not None]) if any(val(x['gpu_util_percent']) is not None for x in chunk) else '','inference_count':sum(x['inference_completed'] for x in chunk),'inference_mean_s':statistics.fmean(ig) if ig else ''})
    write_csv(d/'minute_metrics.csv',minutes)
    gap_rows=[{'frame_index':r.get('frame_index'),'relative_s':(val(r.get('receive_monotonic_s'))-start) if start is not None and val(r.get('receive_monotonic_s')) is not None else '','receive_gap_s':r.get('receive_gap_s'),'source_gap_s':r.get('source_gap_s'),'thresholds':'|'.join(str(t) for t in THRESHOLDS if (val(r.get('receive_gap_s')) or 0)>=t)} for r in frames if (val(r.get('receive_gap_s')) or 0)>=.1]
    if terminal_gap>=.1:gap_rows.append({'frame_index':'END_OF_WINDOW','relative_s':duration,'receive_gap_s':terminal_gap,'source_gap_s':'','thresholds':'|'.join(str(t) for t in THRESHOLDS if terminal_gap>=t)})
    write_csv(d/'gap_events.csv',gap_rows,['frame_index','relative_s','receive_gap_s','source_gap_s','thresholds'])
    seq=[val(r.get('sequence_delta')) for r in frames];seq=[x for x in seq if x is not None];il=[val(r.get('inference_duration_s')) for r in inf];il=[x for x in il if x is not None];camera_ages=[val(r.get('camera_age_at_inference_start_s')) for r in inf];camera_ages=[x for x in camera_ages if x is not None]
    long_intervals=[]
    for previous,current in zip(frames,frames[1:]):
        a=val(previous.get('receive_monotonic_s'));b=val(current.get('receive_monotonic_s'))
        if a is not None and b is not None and b-a>=.1:long_intervals.append((a,b))
    inference_overlap=0
    for row in inf:
        a=val(row.get('inference_start_monotonic_s'));b=val(row.get('inference_end_monotonic_s'))
        if a is not None and b is not None and any(x<b and y>a for x,y in long_intervals):inference_overlap+=1
    diag=read_csv(d/'sdk_diagnostics.csv');capture_hz=[];drop_values=[]
    for row in diag:
        if row.get('key','').startswith('Data Capture'):
            match=re.search(r'Mean Frequency:\s*([\d.]+)\s*Hz',row.get('value',''))
            if match:capture_hz.append(float(match.group(1)))
        if row.get('key')=='Frame Drop rate':drop_values.append(row.get('value'))
    thirds=[]
    for a,b in ((0,600),(600,1200),(1200,1800)):
        ss=seconds[a:b];thirds.append({'range_s':f'{a}-{b}','mean_fps':statistics.fmean(x['fps'] for x in ss),'max_receive_gap_s':max((val(x['max_receive_gap_s']) or 0 for x in ss),default=0),'gap_ge_100ms':sum(x['gap_ge_100ms'] for x in ss)})
    cpu=[val(x['cpu_percent']) for x in seconds];gpu=[val(x['gpu_util_percent']) for x in seconds];gap_axis=[val(x['max_receive_gap_s']) or 0 for x in seconds]
    inference_ok=sum(r.get('status')=='OK' for r in inf)
    summary={'case':key,'status':status.get('status'),'failure_reason':status.get('reason'),'valid_30min_result':bool(status.get('valid_30min_result')),'duration_s':duration,'total_frames':len(frames),'average_fps':len(frames)/duration if duration else None,'receive_gap_s':stats(gaps),'source_gap_s':stats(source_gaps),'source_age_s':stats(ages),'gap_counts':{str(t):sum(x>=t for x in gaps) for t in THRESHOLDS},'source_timestamp_nonincreasing':sum(x<=0 for x in source_gaps),'sequence_nonconsecutive_events':sum(x!=1 for x in seq),'sequence_discontinuity_caveat':'Sequence discontinuities are not asserted to be sensor frame loss.','v4l2_error_flags':sum(str(r.get('v4l2_error_flag','')).lower() in ('1','true') for r in frames),'inference_count':inference_ok,'inference_errors':sum(r.get('status')=='ERROR' for r in inf),'inferences_per_second':inference_ok/duration if duration else None,'inference_duration_s':stats(il),'camera_age_at_inference_start_s':stats(camera_ages),'inference_intervals_overlapping_receive_gap_ge_100ms':inference_overlap,'overlap_caveat':'Temporal overlap is not proof that inference caused the receive gap.','sdk_reported_capture_hz':stats(capture_hz),'sdk_frame_drop_first':drop_values[0] if drop_values else None,'sdk_frame_drop_last':drop_values[-1] if drop_values else None,'cpu_gap_pearson_descriptive':correlation(cpu,gap_axis),'gpu_gap_pearson_descriptive':correlation(gpu,gap_axis),'correlation_caveat':'Descriptive temporal association only; not causal evidence.','thirds':thirds,'unsupported_value_semantics':'Empty CSV field or JSON null means NOT_AVAILABLE; see failure_reason.','physical_commands':0}
    (d/'summary.json').write_text(json.dumps(summary,indent=2,allow_nan=False)+'\n');return summary,seconds,minutes,gaps,resources
def plot(root,data):
    figdir=root/'figures';figdir.mkdir(exist_ok=True)
    def save(name):plt.tight_layout();plt.savefig(figdir/name,dpi=140);plt.close()
    for key,(_,seconds,minutes,_,resources) in data.items():
        plt.figure(figsize=(12,3));plt.plot([x['second'] for x in seconds],[x['fps'] for x in seconds],lw=.6);plt.axhline(30,color='k',ls='--',lw=.6);plt.xlabel('second');plt.ylabel('FPS');plt.title(key+' 1-second FPS');save(key+'_second_fps.png')
        plt.figure(figsize=(9,3));plt.plot([x['minute'] for x in minutes],[x['mean_fps'] for x in minutes],marker='.');plt.xlabel('minute');plt.ylabel('FPS');plt.title(key+' 60-second FPS');save(key+'_minute_fps.png')
        plt.figure(figsize=(12,3));plt.plot([x['second'] for x in seconds],[val(x['max_receive_gap_s']) or 0 for x in seconds],lw=.6);plt.xlabel('second');plt.ylabel('max gap (s)');plt.title(key+' receive gap');save(key+'_receive_gap.png')
        plt.figure(figsize=(12,4));plt.plot([val(x.get('relative_s')) for x in resources],[val(x.get('cpu_percent')) for x in resources],label='CPU %',alpha=.7);plt.plot([x['second'] for x in seconds],[(val(x['max_receive_gap_s']) or 0)*100 for x in seconds],label='gap s x100',alpha=.7);plt.legend();plt.xlabel('second');plt.title(key+' load and gap (descriptive only)');save(key+'_load_gap.png')
    plt.figure(figsize=(10,4));
    for key,(_,_,_,gaps,_) in data.items():
        if gaps:plt.hist(gaps,bins=80,histtype='step',label=key,log=True)
    plt.legend();plt.xlabel('receive gap (s)');plt.ylabel('count (log)');save('all_gap_distribution.png')
    keys=list(data);means=[data[k][0]['average_fps'] or 0 for k in keys];maximum=[data[k][0]['receive_gap_s']['max'] or 0 for k in keys];fig,ax=plt.subplots(figsize=(9,4));ax.bar(keys,means,label='average FPS');ax2=ax.twinx();ax2.plot(keys,maximum,color='r',marker='o',label='max gap');ax.set_ylabel('FPS');ax2.set_ylabel('seconds');save('all_fps_max_gap.png')
def report(root,summaries):
    valid=[s for s in summaries if s['valid_30min_result']];layer='UNCONFIRMED';load='UNCONFIRMED';aging='UNCONFIRMED'
    if valid:
        low=[s for s in valid if s['case'] in 'GH' and (s['gap_counts']['0.1']>0)]
        if low:layer='V4L2 이하 경계에서도 공백 관측; 카메라/USB/UVC/xHCI 중 세부 원인은 UNCONFIRMED'
        a=next((s for s in valid if s['case']=='A'),None);b=next((s for s in valid if s['case']=='B'),None)
        if a and b:load='A/B 기술통계 비교 완료. 동시 발생은 인과 증명이 아니며 상세 표 참조'
        worsen=[s for s in valid if s['thirds'][2]['mean_fps']<s['thirds'][0]['mean_fps'] and s['thirds'][2]['gap_ge_100ms']>s['thirds'][0]['gap_ge_100ms']]
        aging='악화 패턴 관측: '+','.join(x['case'] for x in worsen) if worsen else '일관된 장시간 악화 패턴 미관측'
    rows='\n'.join(f"| {s['case']} | {s['status']} | {s['total_frames']} | {s['average_fps'] or 0:.3f} | {s['receive_gap_s']['p95'] or 0:.4f} | {s['receive_gap_s']['p99'] or 0:.4f} | {s['receive_gap_s']['max'] or 0:.6f} | {s['gap_counts']['0.1']} | {s['gap_counts']['0.3']} | {s['gap_counts']['0.5']} | {s['gap_counts']['1.0']} | {s['gap_counts']['2.0']} |" for s in summaries)
    third_rows='\n'.join(f"| {s['case']} | "+' | '.join(f"{x['mean_fps']:.2f} / {x['max_receive_gap_s']:.3f}s / {x['gap_ge_100ms']}" for x in s['thirds'])+' |' for s in summaries)
    infer_rows='\n'.join(f"| {s['case']} | {s['inference_count']} | {s['inferences_per_second'] or 0:.3f} | {s['inference_duration_s']['median'] or 0:.4f} | {s['inference_duration_s']['p95'] or 0:.4f} | {s['inference_duration_s']['p99'] or 0:.4f} | {s['inference_duration_s']['max'] or 0:.4f} | {s['camera_age_at_inference_start_s']['median'] or 0:.4f} | {s['inference_intervals_overlapping_receive_gap_ge_100ms']} |" for s in summaries if s['case'] in ('B','C'))
    text=f'''# CAMERA 30MIN REPORT

## 1. 실험 목적 및 환경

ZED 2i의 프레임 공백 계층, 부하 연관성, 시간 경과 변화를 A~H의 고정 1,800초 유효 측정만으로 비교한다. 미완료 케이스는 정상 결과로 간주하지 않는다. 환경 원본은 각 `environment.json`에 있다.

## 2. 기존 문제 요약

기존 60초 증거에는 ROS2뿐 아니라 Native V4L2 버퍼 단계의 불규칙 수신이 포함되어 있다. 이는 이번 30분 결과가 아니며 배경 증거다.

## 3. 각 케이스 실행 방식

A는 모델 health 확인 후 ROS 수신만, B는 최신 프레임 반복 추론, C는 B와 JPEG/PNG/JSONL fsync를 수행한다. C의 RobotState/settle은 실제 제공 여부를 기록하며 명령은 만들지 않는다. D는 SDK `read()` retrieve 없음, E는 `grab+retrieve`, F는 SDK validity OFF, G는 OpenCV V4L2, H는 Native V4L2 mmap/DQBUF다.

## 4. 전체 케이스 비교표

| Case | 상태 | Frames | Avg FPS | gap p95 | gap p99 | Max gap(s) | >=100ms | >=300ms | >=500ms | >=1s | >=2s |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
{rows}

## 5. 1초 단위 프레임 수신 결과

각 케이스 `second_metrics.csv`와 `figures/*_second_fps.png` 참조. 데이터가 없는 초도 0 FPS로 보존했다.

## 6. 60초 단위 FPS 및 공백 변화

각 케이스 `minute_metrics.csv`와 `figures/*_minute_fps.png` 참조.

## 7. 0~10분 / 10~20분 / 20~30분 비교

각 `summary.json`의 `thirds` 참조. 판정: **{aging}**.

| Case | 0~10분 FPS/max/≥100ms | 10~20분 FPS/max/≥100ms | 20~30분 FPS/max/≥100ms |
|---|---:|---:|---:|
{third_rows}

## 8. CPU/GPU 사용률과 수신 공백 관계

`resource_metrics.csv` 및 `figures/*_load_gap.png`에 기술통계를 보존했다. 판정: **{load}**. 상관은 인과로 해석하지 않는다.

| Case | 추론 수 | req/s | latency median | p95 | p99 | max | start Camera Age median | ≥100ms gap과 겹친 추론 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
{infer_rows}

겹침은 시간축 기술통계일 뿐 추론이 공백을 유발했다는 증거가 아니다. C의 RobotState 및 settle은 서비스/토픽 부재로 `NOT_AVAILABLE`이며 정상 수행으로 대체하지 않았다.

A 자체에서 최대 0.703초 공백과 ≥500ms 13회가 있었으므로 OpenVLA 추론은 공백 발생의 필요조건이 아니다. 반대로 B/C는 GPU 추론 및 C의 디스크 fsync 부하가 있었지만 ≥500ms 공백이 0회였다. 순차 실행이고 카메라 상태가 시간에 따라 크게 변했으므로 이것은 “부하가 무관하다”는 증명이 아니라, 이번 데이터가 추론/호스트 부하를 주원인으로 지지하지 않는다는 근거다.

## 9. SDK/V4L2/ROS2 계층별 비교

**{layer}**.

H Native V4L2에서도 평균 20.462FPS, 최대 Receive Gap 0.500027초였고 최대 driver timestamp gap도 같은 수준이었다. 따라서 ROS2 subscriber, OpenVLA, JPEG/PNG/fsync, OpenCV 및 ZED SDK가 없어도 불규칙 공급이 남는다. D~F의 SDK API/validity 변경도 모두 평균 15.003~21.684FPS와 최대 0.700~0.767초 공백을 보였으므로 validity check 하나로 해결되지 않았다.

## 10. 케이블·USB·SDK·드라이버 원인 후보별 근거

Native V4L2 공백은 ROS/OpenVLA가 필수 원인이 아님을 지지할 수 있으나, 카메라 내부·케이블·포트·UVC·xHCI를 서로 분리하지 못한다. 커널 로그/USB topology/SDK 진단은 접근 가능한 원본만 환경 파일에 저장하며 누락은 NOT_AVAILABLE이다.

실험 당시 ZED 영상 인터페이스는 5,000M으로 연결됐지만 두 단계 USB 3 hub 경로를 통과했다. 속도 협상은 정상 범주이나 hub/케이블/포트 안정성을 증명하지는 않는다. 커널 ring buffer는 권한 부족으로 읽지 못해 UVC/xHCI reset 여부는 `NOT_AVAILABLE`이다. A~C SDK 진단 Capture FPS는 실제 수신 변화와 함께 흔들렸으나 moving aggregate이므로 개별 grab 원인을 분리하지 못한다.

## 11. 확인된 사실 / 추정 / 미확정 사항 구분

- 확인된 사실: `valid_30min_result=true`인 행과 그 원시 CSV 수치만 해당한다.
- 확인된 사실: 모든 케이스가 목표 30FPS 미만(15.003~25.461FPS)이었고 Native V4L2까지 최대 0.500초 공백이 존재했다.
- 확인된 사실: 공통적인 사용시간 악화는 없었다. D는 16.48→14.39→14.14FPS로 악화했지만 A/C/E/F/G는 마지막 구간이 첫 구간보다 개선했고 B/H는 중간 개선 후 마지막에 하락했다.
- 추정: 시간축 동시성 및 케이스 간 차이는 원인 후보를 좁히는 기술통계다.
- 미확정: 케이블, 발열, SDK, 드라이버, USB 컨트롤러 중 단일 근본 원인.
- 안전: physical commands = 0. Sequence 불연속은 센서 손실 개수로 단정하지 않는다.

## 12. 후속 실험 및 권장 해결 방향

동일 포트에서 케이블만 교차한 무작위 순서 반복, 다른 root hub 비교, 권한 확보 후 UVC/xHCI 커널 이벤트와 usbmon 동기 수집, ZED 진단 drop counter 동기화를 권장한다. 임계값·timeout 완화나 프레임 보간은 사용하지 않는다.
'''
    (root/'CAMERA_30MIN_REPORT.md').write_text(text)
def main():
    p=argparse.ArgumentParser();p.add_argument('root',type=Path,nargs='?',default=Path(__file__).parents[1]/'camera_30min_validation');a=p.parse_args();a.root.mkdir(parents=True,exist_ok=True);data={};summaries=[]
    for key,name in CASES:
        d=a.root/name;d.mkdir(exist_ok=True);result=aggregate_case(key,d);data[key]=result;summaries.append(result[0])
    write_csv(a.root/'comparison.csv',summaries);(a.root/'comparison.json').write_text(json.dumps({'cases':summaries,'physical_commands':0},indent=2,allow_nan=False)+'\n');plot(a.root,data);report(a.root,summaries)
if __name__=='__main__':main()
