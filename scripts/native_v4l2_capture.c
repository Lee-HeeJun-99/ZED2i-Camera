#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <linux/videodev2.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <time.h>
#include <unistd.h>

struct mapped { void *ptr; size_t len; };
static int xioctl(int fd, unsigned long request, void *arg) {
  int rc; do { rc=ioctl(fd,request,arg); } while(rc<0 && errno==EINTR); return rc;
}
static double mono(void) { struct timespec t; clock_gettime(CLOCK_MONOTONIC,&t); return t.tv_sec+t.tv_nsec/1e9; }
static uint32_t fourcc(const char *s) { return v4l2_fourcc(s[0],s[1],s[2],s[3]); }
int main(int argc,char **argv) {
  if(argc!=8){fprintf(stderr,"usage: %s DEVICE WIDTH HEIGHT FPS FOURCC DURATION OUTPUT\n",argv[0]);return 2;}
  const char *device=argv[1],*fmt=argv[5],*output=argv[7]; int width=atoi(argv[2]),height=atoi(argv[3]),fps=atoi(argv[4]); double duration=atof(argv[6]);
  int fd=open(device,O_RDWR|O_NONBLOCK); if(fd<0){perror("open");return 3;}
  struct v4l2_capability cap={0}; if(xioctl(fd,VIDIOC_QUERYCAP,&cap)<0){perror("VIDIOC_QUERYCAP");return 4;}
  struct v4l2_format form={.type=V4L2_BUF_TYPE_VIDEO_CAPTURE}; form.fmt.pix.width=width;form.fmt.pix.height=height;form.fmt.pix.pixelformat=fourcc(fmt);form.fmt.pix.field=V4L2_FIELD_ANY;
  if(xioctl(fd,VIDIOC_S_FMT,&form)<0){perror("VIDIOC_S_FMT");return 5;}
  struct v4l2_streamparm parm={.type=V4L2_BUF_TYPE_VIDEO_CAPTURE}; parm.parm.capture.timeperframe.numerator=1;parm.parm.capture.timeperframe.denominator=fps;
  if(xioctl(fd,VIDIOC_S_PARM,&parm)<0) perror("VIDIOC_S_PARM");
  struct v4l2_requestbuffers req={.count=8,.type=V4L2_BUF_TYPE_VIDEO_CAPTURE,.memory=V4L2_MEMORY_MMAP};
  if(xioctl(fd,VIDIOC_REQBUFS,&req)<0||req.count<2){perror("VIDIOC_REQBUFS");return 6;}
  struct mapped *maps=calloc(req.count,sizeof(*maps));
  for(unsigned i=0;i<req.count;i++){struct v4l2_buffer b={.type=req.type,.memory=req.memory,.index=i};if(xioctl(fd,VIDIOC_QUERYBUF,&b)<0){perror("VIDIOC_QUERYBUF");return 7;}maps[i].len=b.length;maps[i].ptr=mmap(NULL,b.length,PROT_READ|PROT_WRITE,MAP_SHARED,fd,b.m.offset);if(maps[i].ptr==MAP_FAILED){perror("mmap");return 8;}if(xioctl(fd,VIDIOC_QBUF,&b)<0){perror("VIDIOC_QBUF");return 9;}}
  enum v4l2_buf_type type=V4L2_BUF_TYPE_VIDEO_CAPTURE;if(xioctl(fd,VIDIOC_STREAMON,&type)<0){perror("VIDIOC_STREAMON");return 10;}
  FILE *out=fopen(output,"wx");if(!out){perror("output");return 11;}fprintf(out,"frame_index,receive_monotonic_s,receive_wall_s,source_timestamp_s,sequence,bytesused,v4l2_flags,error_flag\n");fflush(out);
  double first=-1;unsigned long index=0;int status=0;
  while(1){if(first>=0 && mono()-first>=duration)break;struct pollfd p={.fd=fd,.events=POLLIN|POLLERR};int pr=poll(&p,1,1000);if(pr<0&&errno==EINTR)continue;if(pr<=0){if(pr<0)perror("poll");continue;}struct v4l2_buffer b={.type=type,.memory=V4L2_MEMORY_MMAP};if(xioctl(fd,VIDIOC_DQBUF,&b)<0){if(errno==EAGAIN)continue;perror("VIDIOC_DQBUF");status=12;break;}double now=mono();struct timespec rt;clock_gettime(CLOCK_REALTIME,&rt);double wall=rt.tv_sec+rt.tv_nsec/1e9;if(first<0)first=now;if(now-first>=duration){xioctl(fd,VIDIOC_QBUF,&b);break;}double source=b.timestamp.tv_sec+b.timestamp.tv_usec/1e6;fprintf(out,"%lu,%.9f,%.9f,%.9f,%u,%u,%u,%d\n",index++,now,wall,source,b.sequence,b.bytesused,b.flags,!!(b.flags&V4L2_BUF_FLAG_ERROR));if(index%30==0)fflush(out);if(xioctl(fd,VIDIOC_QBUF,&b)<0){perror("VIDIOC_QBUF");status=13;break;}}
  fflush(out);fsync(fileno(out));fclose(out);xioctl(fd,VIDIOC_STREAMOFF,&type);for(unsigned i=0;i<req.count;i++)munmap(maps[i].ptr,maps[i].len);free(maps);close(fd);return status;
}
