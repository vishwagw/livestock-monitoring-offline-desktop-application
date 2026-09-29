import { describe, expect, it } from 'vitest'

import { classifyCsvHeader, classifyFile, normaliseHeader, splitCsvLine } from '@shared/fileKinds'

describe('normaliseHeader', () => {
  it('matches the Python column normalisation', () => {
    expect(normaliseHeader('Height Above Takeoff (feet)')).toBe('height_above_takeoff')
    expect(normaliseHeader('﻿Gimbal-Pitch [deg]')).toBe('gimbal_pitch')
    expect(normaliseHeader('  time.s ')).toBe('time_s')
  })
})

describe('splitCsvLine', () => {
  it('handles quoted commas', () => {
    expect(splitCsvLine('a,"b,c",d')).toEqual(['a', 'b,c', 'd'])
    expect(splitCsvLine('"say ""hi""",2')).toEqual(['say "hi"', '2'])
  })
})

describe('classifyFile', () => {
  it('recognises DJI SRT logs', () => {
    const srt = '1\n00:00:00,000 --> 00:00:00,033\n<font size="28">FrameCnt: 1 [latitude: -33.1] [longitude: 151.2]'
    expect(classifyFile('DJI_0001.SRT', srt)).toEqual({ kind: 'telemetry', detail: 'DJI SRT subtitle log' })
  })

  it.each([
    ['image,latitude,longitude,relative_altitude,gimbal_yaw', 'telemetry'],
    ['Time(millisecond),Latitude,Longitude,Height Above Takeoff (feet)', 'telemetry'],
    ['image,frame,xmin,ymin,xmax,ymax,label,confidence', 'detections'],
    ['frame,cx,cy,w,h,class,score', 'detections'],
    ['frame_id,latitude,longitude,altitude_agl_m,heading_deg,image_width,image_height,fov_deg,x,y', 'dataset'],
    ['name,notes', 'unknown']
  ])('classifies CSV header %s as %s', (header, kind) => {
    expect(classifyCsvHeader(header).kind).toBe(kind)
    expect(classifyFile('log.csv', `${header}\n1,2,3`).kind).toBe(kind)
  })

  it('classifies JSON and YOLO files', () => {
    expect(classifyFile('flight.json', '{"metadata": {}, "frames": [').kind).toBe('dataset')
    expect(classifyFile('coco.json', '{"images": [], "annotations": [').kind).toBe('detections')
    expect(classifyFile('boxes.json', '[{"image": "a.jpg", "bbox": [1,2,3,4]}]').kind).toBe('detections')
    expect(classifyFile('DJI_0001.txt', '0 0.5 0.5 0.1 0.2 0.91\n').kind).toBe('detections')
    expect(classifyFile('notes.txt', 'hello world').kind).toBe('unknown')
    expect(classifyFile('photo.jpg', '').kind).toBe('unknown')
  })
})
