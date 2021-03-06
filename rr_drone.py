#!/usr/bin/python
"""
  Robotem Rovne competition in Pisek. See www.kufr.cz
  usage:
       ./rr_drone.py <TODO>
"""
# based on airrace_drone.py
import sys
import datetime
import multiprocessing
import cv2
import math
import numpy as np

from pave import PaVE, isIFrame, frameNumber, timestamp, correctTimePeriod, frameEncodedWidth, frameEncodedHeight

from sourcelogger import SourceLogger
from ardrone2 import ARDrone2, ManualControlException, manualControl, normalizeAnglePIPI, distance
import viewlog
from line import Line
from pose import Pose

from airrace import main as imgmain # image debugging TODO move to launcher
from airrace import saveIndexedImage, FRAMES_PER_INDEX

import cvideo

MAX_ALLOWED_SPEED = 0.8
MAX_ALLOWED_VIDEO_DELAY = 2.0 # in seconds, then it will wait (desiredSpeed = 0.0)

MIN_ROAD_AREA = 18000 # filter out small areas

ROAD_WIDTH_MIN = 1.0 # RR-Pisek 2.5
ROAD_WIDTH_MAX = 5.0 # RR-Pisek 6.0
ROAD_WIDTH_VARIANCE = 2.0

ROAD_WIDTH = 2.6 # garden, 4.35 # expected width in Pisek (was 3.0)
DEFAULT_REF_LINE = None  # TODO fill proper Line((0,0), magicEnd)
MAX_REF_LINE_ANGLE = math.radians(20)
MAX_REF_LINE_DIST = 2.0

g_mser = None

def approx4pts( poly ):
  ret = cv2.approxPolyDP( poly, epsilon = 30, closed=True )
  if len(ret) == 4:
    return ret
  if len(ret) < 4:
    return cv2.approxPolyDP( poly, epsilon = 20, closed=True )
  return cv2.approxPolyDP( poly, epsilon = 50, closed=True )

def trapezoid2line( t ):
  if len(t) == 4:
    if (max( t[0][1], t[1][1] ) < min (t[2][1], t[3][1])) or (min( t[0][1], t[1][1] ) > max (t[2][1], t[3][1])) :
      begin,end = [((t[0][0]+t[1][0])/2, (t[0][1]+t[1][1])/2), ((t[2][0]+t[3][0])/2, (t[2][1]+t[3][1])/2)]
    else:
      begin,end = [((t[0][0]+t[3][0])/2, (t[0][1]+t[3][1])/2), ((t[2][0]+t[1][0])/2, (t[2][1]+t[1][1])/2)]
    if begin[1] < end[1]:
      begin,end = end,begin
    return [begin,end]

def rect2BLBRTRTL( rect ):
  if len(rect) == 4:
    s = [(x,y) for y,x in sorted( [(y,x) for x,y in rect] )] # sort be Y
    if s[0][0] < s[1][0]:
      TL,TR = s[:2]
    else:
      TR,TL = s[:2]
    if s[2][0] < s[3][0]:
      BL,BR = s[2:]
    else:
      BR,BL = s[2:]
    return BL,BR,TR,TL

def drawArrow( img, pt1, pt2, color, thickness=1 ):
  # inspiration from http://stackoverflow.com/questions/10161351/opencv-how-to-plot-velocity-vectors-as-arrows-in-using-single-static-image
  cv2.line(img, pt1, pt2, color, thickness)
  l = math.hypot(pt1[0]-pt2[0], pt1[1]-pt2[1])
  if l > 0.1:
    spinSize = l/4.
    spinAngle = math.radians(15)
    angle = math.atan2(pt1[1]-pt2[1], pt1[0]-pt2[0])
    pt = int(pt2[0] + spinSize * math.cos(angle+spinAngle)), int(pt2[1] + spinSize * math.sin(angle+spinAngle))
    cv2.line(img, pt, pt2, color, thickness)
    pt = int(pt2[0] + spinSize * math.cos(angle-spinAngle)), int(pt2[1] + spinSize * math.sin(angle-spinAngle))
    cv2.line(img, pt, pt2, color, thickness)

def processFrame( frame, debug=False ):
  global g_mser
  result = []
  allHulls = []
  selectedHulls = []
#  for stripOffset in [ -100, 0, +100, +200 ]:
  for stripOffset in [ +100 ]:
    midY = frame.shape[0]/2+stripOffset
    stripWidth = 120
    if g_mser == None:
      g_mser = cv2.MSER( _delta = 10, _min_area=100, _max_area=stripWidth*1000 )
    imgStrip = frame[ midY:midY+stripWidth, 0:frame.shape[1] ]
    b,g,r = cv2.split( imgStrip )
    gray = b
    contours = g_mser.detect(gray, None)
    hulls = []
    selected = None
    for cnt in contours:
      (x1,y1),(x2,y2) = np.amin( cnt, axis=0 ), np.amax( cnt, axis=0 )
      if y1 == 0 and y2 == stripWidth-1: # i.e. whole strip
        if x1 > 0 and x2 < frame.shape[1]-1:
          hull = cv2.convexHull(cnt.reshape(-1, 1, 2))
          for h in hull:
            h[0][1] += midY
          hulls.append( hull )
          # select the one with the smallest area
          if len(cnt) >= MIN_ROAD_AREA:
            rect = rect2BLBRTRTL([(a[0][0],a[0][1]) for a in approx4pts( hull )] )
            if rect != None:
              bl,br,tr,tl = rect
              tmpInt = Line(bl,tl).intersect( Line(br,tr) )
              if tmpInt != None:
                p = tuple([int(x) for x in tmpInt])
                if br[0]-bl[0] > tr[0]-tl[0] and p[1] > 0 and p[1] < frame.shape[0]:
                  # make sure that direction is forward and in the image
                  result.append( rect )
#                if selected == None or selected[0] > len(cnt):
#                  selected = len(cnt), hull, rect
#    if selected:
#      result.append( selected[2] )
    if debug:
      allHulls.extend( hulls )
      if selected != None:
        selectedHulls.append( selected[1] )
  
  if debug:
    cv2.polylines(frame, allHulls, 2, (0, 255, 0), 2)
    if len(selectedHulls) > 0:
      cv2.polylines(frame, selectedHulls, 2, (0, 0, 0), 2)
#    for trapezoid in result:
#      cv2.drawContours( frame,[np.int0(trapezoid)],0,(255,0,0),2)
#    for trapezoid in result:
#      bl,br,tr,tl = trapezoid
#      p = tuple([int(x) for x in Line(bl,tl).intersect( Line(br,tr) )])
#      cv2.circle( frame, p, 10, (0,255,128), 3 )
#      navLine = trapezoid2line( trapezoid )
#      if navLine:
#        drawArrow(frame, navLine[0], navLine[1], (0,0,255), 4)
    cv2.imshow('image', frame)
    saveIndexedImage( frame )
  return result

def timeName( prefix, ext ):
  dt = datetime.datetime.now()
  filename = prefix + dt.strftime("%y%m%d_%H%M%S.") + ext
  return filename

g_pave = None
g_processingEnabled = True # shared multiprocess variable (!)

g_img = None


def wrapper( packet ):
  global g_pave
  global g_img
  if g_pave == None:
    g_pave = PaVE()
    cvideo.init()
    g_img = np.zeros([720,1280,3], dtype=np.uint8)
  g_pave.append( packet )
  header,payload = g_pave.extract()
  while payload:
    if isIFrame( header ):
      w,h = frameEncodedWidth(header), frameEncodedHeight(header)
      if g_img.shape[0] != h or g_img.shape[1] != w:
        print g_img.shape, (w,h)
        g_img = np.zeros([h,w,3], dtype=np.uint8)
      ret = cvideo.frame( g_img, isIFrame(header) and 1 or 0, payload )
      frame = g_img
      assert ret
      if ret:
        result = None
        if g_processingEnabled:
          result = processFrame( frame, debug=False )
        return (frameNumber( header ), timestamp(header)), result
    header,payload = g_pave.extract()

g_queueResults = multiprocessing.Queue()

def getOrNone():
  if g_queueResults.empty():
    return None
  return g_queueResults.get()


def project2plane( imgCoord, coord, height, heading, angleFB, angleLR ):
  FOW = math.radians(70)
  EPS = 0.0001
  x,y = imgCoord[0]-1280/2, 720/2-imgCoord[1]
  angleLR = -angleLR # we want to compensate the turn
  x,y = x*math.cos(angleLR)-y*math.sin(angleLR), y*math.cos(angleLR)+x*math.sin(angleLR)
  h = -x/1280*FOW + heading
  tilt = y/1280*FOW + angleFB
  if tilt > -EPS:
    return None # close to 0.0 AND projection behind drone
  dist = height/math.tan(-tilt)
  return (coord[0]+math.cos(h)*dist , coord[1]+math.sin(h)*dist)


def roadWidthArr( roadPts ):
  "points are expected to be 4 already sorted by BLBRTRRL image order"
  assert len(roadPts) == 4, roadPts
  bl,br,tr,tl = roadPts
  lineL = Line(bl,tl)
  lineR = Line(br,tr)
  return abs(lineR.signedDistance(bl)),abs(lineL.signedDistance(br)),abs(lineL.signedDistance(tr)),abs(lineR.signedDistance(tl))

def roadWidth( roadPts ):
  return roadWidthArr( roadPts)[0]

def chooseBestWidth( rects, coord, height, heading, angleFB, angleLR, debugImg=None, color=None ):
  best = None
  for rect in rects:
    navLine = trapezoid2line( rect )
    if navLine:
      start = project2plane( navLine[0], coord, height, heading, angleFB, angleLR )
      end = project2plane( navLine[1], coord, height, heading, angleFB, angleLR )
      if start and end:
        preRefLine = Line(start, end)
        obst = []
        for p in rect2BLBRTRTL(rect):
          p2d = project2plane( p, coord, height, heading, angleFB, angleLR )
          if p2d:
            obst.append( p2d )
            print "COORD", p2d
        if len( obst ) == 4:
          widthArr = roadWidthArr( obst )
          print "(%.1f %.1f %.1f %.1f)" % widthArr, 
          widthMin,widthMax = min(widthArr), max(widthArr)
          if widthMin >= ROAD_WIDTH_MIN and widthMax <= ROAD_WIDTH_MAX and widthMax-widthMin <= ROAD_WIDTH_VARIANCE:
            w = (widthArr[0]+widthArr[1])/2.0
            if best == None or abs(best[0]-ROAD_WIDTH) > abs(w-ROAD_WIDTH):
              best = w,preRefLine,rect
  if best == None:
    print "  Width: None"
    return None
  print "  Width %.2f" % best[0]
  if debugImg != None:
    cv2.drawContours( debugImg, [np.int0(best[2])],0,(255,0,0),2 )
    navLine = trapezoid2line( best[2] )
    if navLine:
      if color == None:
        color = (0,0,255)
      drawArrow( debugImg, navLine[0], navLine[1], color, 4)

  return best[1]

class RobotemRovneDrone( ARDrone2 ):
  def __init__( self, replayLog=None, speed = 0.2, skipConfigure=False, metaLog=None, console=None ):
    self.loggedVideoResult = None
    self.lastImageResult = None
    self.videoHighResolution = True
    ARDrone2.__init__( self, replayLog, speed, skipConfigure, metaLog, console )
    if replayLog == None:
      name = timeName( "logs/src_cv2_", "log" ) 
      metaLog.write("cv2: "+name+'\n' )
      self.loggedVideoResult = SourceLogger( getOrNone, name ).get
      self.startVideo( wrapper, g_queueResults, record=True, highResolution=self.videoHighResolution )
    else:
      assert metaLog
      self.loggedVideoResult = SourceLogger( None, metaLog.getLog("cv2:") ).get
      self.startVideo( record=True, highResolution=self.videoHighResolution )

  def update( self, cmd="AT*COMWDG=%i,\r" ):
    ARDrone2.update( self, cmd )
    if self.loggedVideoResult != None:
      self.lastImageResult = self.loggedVideoResult()


def downloadOldVideo( drone, timeout ):
  "download video if it is delayed more than MAX_VIDEO_DELAY"
  global g_processingEnabled
  restoreProcessing = g_processingEnabled
  print "Waiting for video to start ..."
  startTime = drone.time
  while drone.time < startTime + timeout:
    if drone.lastImageResult != None:
      (frameNumber, timestamp), rects = drone.lastImageResult
      videoTime = correctTimePeriod( timestamp/1000., ref=drone.time )
      videoDelay = drone.time - videoTime
      print videoDelay
      if videoDelay < MAX_ALLOWED_VIDEO_DELAY:
        print "done"
        break
      g_processingEnabled = False
    drone.update()
  g_processingEnabled = restoreProcessing

def competeRobotemRovne( drone, desiredHeight = 1.5 ):
  drone.speed = 0.1
  maxVideoDelay = 0.0
  maxControlGap = 0.0
  desiredSpeed = MAX_ALLOWED_SPEED
  refLine = DEFAULT_REF_LINE
  
  try:
    drone.wait(1.0)
    drone.setVideoChannel( front=True )
    downloadOldVideo( drone, timeout=20.0 )
    drone.takeoff()
    poseHistory = []
    startTime = drone.time
    while drone.time < startTime + 1.0:
      drone.update("AT*PCMD=%i,0,0,0,0,0\r") # drone.hover(1.0)
      # TODO sonar/vision altitude
      poseHistory.append( (drone.time, (drone.coord[0], drone.coord[1], drone.heading), (drone.angleFB, drone.angleLR), None) )
    magnetoOnStart = drone.magneto[:3]
    print "NAVI-ON"
    startTime = drone.time
    sx,sy,sz,sa = 0,0,0,0
    lastUpdate = None
    while drone.time < startTime + 600.0:

      altitude = desiredHeight
      if drone.altitudeData != None:
        altVision = drone.altitudeData[0]/1000.0
        altSonar = drone.altitudeData[3]/1000.0
        altitude = (altSonar+altVision)/2.0
        # TODO selection based on history? panic when min/max too far??
        if abs(altSonar-altVision) > 0.5:
          print altSonar, altVision
          altitude = max( altSonar, altVision ) # sonar is 0.0 sometimes (no ECHO)

      sz = max( -0.2, min( 0.2, desiredHeight - altitude ))
      if altitude > 2.5:
        # wind and "out of control"
        sz = max( -0.5, min( 0.5, desiredHeight - altitude ))

      sx = max( 0, min( drone.speed, desiredSpeed - drone.vx ))

      if drone.lastImageResult:
        lastUpdate = drone.time
        assert len( drone.lastImageResult ) == 2 and len( drone.lastImageResult[0] ) == 2, drone.lastImageResult
        (frameNumber, timestamp), rects = drone.lastImageResult
        viewlog.dumpVideoFrame( frameNumber, timestamp )

        # keep history small
        videoTime = correctTimePeriod( timestamp/1000., ref=drone.time )
        videoDelay = drone.time - videoTime
        if videoDelay > 1.0:
          print "!DANGER! - video delay", videoDelay
        maxVideoDelay = max( videoDelay, maxVideoDelay )
        toDel = 0
        for oldTime, oldPose, oldAngles, oldAltitude in poseHistory:
          toDel += 1
          if oldTime >= videoTime:
            break
        poseHistory = poseHistory[:toDel]

        tiltCompensation = Pose(desiredHeight*oldAngles[0], desiredHeight*oldAngles[1], 0) # TODO real height?
        print "FRAME", frameNumber/15, "[%.1f %.1f]" % (math.degrees(oldAngles[0]), math.degrees(oldAngles[1])),
#        print "angle", math.degrees(drone.angleFB-oldAngles[0]), math.degrees(drone.angleLR-oldAngles[1])
        if rects != None and len(rects) > 0:
          # note, that "rects" could be None, when video processing is OFF (g_processingEnabled==False)
          if oldAltitude == None:
            oldAltitude = altitude
          debugImg = None
          if drone.replayLog != None:
            debugFilename = "tmp_%04d.jpg" % (frameNumber/FRAMES_PER_INDEX)            
            debugImg = cv2.imread( debugFilename )
          line = chooseBestWidth( rects, coord=oldPose[:2], height=oldAltitude, 
              heading=oldPose[2], angleFB=oldAngles[0], angleLR=oldAngles[1], debugImg=debugImg )
          if line:
            rejected = False
            if refLine != None:
              print "%.1fdeg %.2fm" % (math.degrees(normalizeAnglePIPI(refLine.angle - line.angle)), refLine.signedDistance( line.start ))
              if abs(normalizeAnglePIPI(refLine.angle - line.angle)) < MAX_REF_LINE_ANGLE and \
                  abs(refLine.signedDistance( line.start )) < MAX_REF_LINE_DIST:
                refLine = line
              else:
                rejected = True
            else:
              refLine = line
            viewlog.dumpBeacon( line.start, index=3 )
            viewlog.dumpBeacon( line.end, index=3 )
            if drone.replayLog != None:
              if rejected:
                # redraw image with yellow color
                chooseBestWidth( rects, coord=oldPose[:2], height=oldAltitude, 
                    heading=oldPose[2], angleFB=oldAngles[0], angleLR=oldAngles[1], debugImg=debugImg, color=(0,255,255) )
              cv2.imwrite( debugFilename, debugImg )
        else:
          print rects

        desiredSpeed = MAX_ALLOWED_SPEED
        if videoDelay > MAX_ALLOWED_VIDEO_DELAY:
          desiredSpeed = 0.0

        if drone.battery < 10:
          print "BATTERY LOW!", drone.battery

        # height debugging
        #print "HEIGHT\t%d\t%d\t%.2f\t%d\t%d\t%d\t%d\t%d\t%d" % tuple([max([0]+[w for ((x,y),(w,h),a) in rects])] + list(drone.altitudeData[:4]) + list(drone.pressure) )

      # error definition ... if you substract that you get desired position or angle
      # error is taken from the path point of view, x-path direction, y-positive left, angle-anticlockwise
      errY, errA = 0.0, 0.0
      if refLine:
        errY = refLine.signedDistance( drone.coord )
        errA = normalizeAnglePIPI( drone.heading - refLine.angle )

      # get the height first
      if drone.coord[2] < desiredHeight - 0.1 and drone.time-startTime < 5.0:
        sx = 0.0
      # error correction
      # the goal is to have errY and errA zero in 1 second -> errY defines desired speed at given distance from path
      sy = max( -0.2, min( 0.2, -errY-drone.vy ))/2.0
      
      # there is no drone.va (i.e. derivative of heading) available at the moment ... 
      sa = max( -0.1, min( 0.1, -errA/2.0 ))*1.35*(desiredSpeed/0.4) # originally set for 0.4=OK

#      print "%0.2f\t%d\t%0.2f\t%0.2f\t%0.2f" % (errY, int(math.degrees(errA)), drone.vy, sy, sa)
      prevTime = drone.time
      drone.moveXYZA( sx, sy, sz, sa )
      maxControlGap = max( drone.time - prevTime, maxControlGap )
      poseHistory.append( (drone.time, (drone.coord[0], drone.coord[1], drone.heading), (drone.angleFB, drone.angleLR), altitude) )
    print "NAVI-OFF", drone.time - startTime
    drone.hover(0.5)
    drone.land()
    drone.setVideoChannel( front=True )    
  except ManualControlException, e:
    print "ManualControlException"
    if drone.ctrlState == 3: # CTRL_FLYING=3 ... i.e. stop the current motion
      drone.hover(0.1)
    drone.land()
  drone.wait(1.0)
  drone.stopVideo()
  print "MaxVideoDelay", maxVideoDelay
  print "MaxControlGap", maxControlGap
  print "Battery", drone.battery


if __name__ == "__main__":
  if len(sys.argv) > 2 and sys.argv[1] == "img":
    imgmain( sys.argv[1:], processFrame )
    sys.exit( 0 )
  import launcher
  launcher.launch( sys.argv, RobotemRovneDrone, competeRobotemRovne )

