import os
import os.path as osp
import cv2
import logging
import argparse
import motmetrics as mm
import numpy as np
import pandas as pd

import torch
from tracker.multitracker import JDETracker
from utils import visualization as vis
from utils.log import logger
from utils.timer import Timer
from utils.evaluation import Evaluator
from utils.parse_config import parse_model_cfg
import utils.datasets as datasets
from utils.utils import *
import datetime


def write_results(filename, results, data_type):
    if data_type == 'mot':
        save_format = '{frame},{id},{x1},{y1},{w},{h},1,-1,-1,-1\n'
    elif data_type == 'kitti':
        save_format = '{frame} {id} pedestrian 0 0 -10 {x1} {y1} {x2} {y2} -10 -10 -10 -1000 -1000 -1000 -10\n'
    else:
        raise ValueError(data_type)

    with open(filename, 'w') as f:
        for frame_id, tlwhs, track_ids in results:
            if data_type == 'kitti':
                frame_id -= 1
            for tlwh, track_id in zip(tlwhs, track_ids):
                if track_id < 0:
                    continue
                x1, y1, w, h = tlwh
                x2, y2 = x1 + w, y1 + h
                line = save_format.format(frame=frame_id, id=track_id, x1=x1, y1=y1, x2=x2, y2=y2, w=w, h=h)
                f.write(line)
    logger.info('save results to {}'.format(filename))

def write_detection_results(filename, results, data_type):

    filename = filename.replace(".txt", "_det.txt")

    if data_type == 'mot':
        save_format = '{frame},{id},{x1},{y1},{w},{h},1,-1,-1,-1\n'
    elif data_type == 'kitti':
        save_format = '{frame} {id} pedestrian 0 0 -10 {x1} {y1} {x2} {y2} -10 -10 -10 -1000 -1000 -1000 -10\n'
    else:
        raise ValueError(data_type)

    with open(filename, 'w') as f:
        for frame_id, tlwhs, track_ids in results:
            for tlwh, track_id in zip(tlwhs, track_ids):
                x1, y1, w, h = tlwh
                x2, y2 = x1 + w, y1 + h
                line = save_format.format(frame=frame_id, id=-1, x1=x1, y1=y1, x2=x2, y2=y2, w=w, h=h)
                f.write(line)
    logger.info('save results to {}'.format(filename))


def eval_seq(opt, dataloader, data_type, result_filename, save_dir=None, show_image=True, frame_rate=30):
    '''
       Processes the video sequence given and provides the output of tracking result (write the results in video file)

       It uses JDE model for getting information about the online targets present.

       Parameters
       ----------
       opt : Namespace
             Contains information passed as commandline arguments.

       dataloader : LoadVideo
                    Instance of LoadVideo class used for fetching the image sequence and associated data.

       data_type : String
                   Type of dataset corresponding(similar) to the given video.

       result_filename : String
                         The name(path) of the file for storing results.

       save_dir : String
                  Path to the folder for storing the frames containing bounding box information (Result frames).

       show_image : bool
                    Option for shhowing individial frames during run-time.

       frame_rate : int
                    Frame-rate of the given video.

       Returns
       -------
       (Returns are not significant here)
       frame_id : int
                  Sequence number of the last sequence
       '''

    if save_dir:
        mkdir_if_missing(save_dir)
    tracker = JDETracker(opt, frame_rate=frame_rate)
    timer = Timer()
    results = []
    detection_results = []
    frame_id = 0
    for path, img, img0 in dataloader:
        if frame_id % 20 == 0:
            logger.info('Processing frame {} ({:.2f} fps)'.format(frame_id, 1./max(1e-5, timer.average_time)))
            #if frame_id != 0:
                # print('results_det[i,:] example: ')
                # print(results_det[frame_id-1][:][:])
                # print('results[i,:] example: ')
                # print(results[frame_id-1][:][:])

        # run tracking
        timer.tic()
        blob = torch.from_numpy(img).cuda().unsqueeze(0)
        online_targets = tracker.update(blob, img0)
        online_tlwhs = []
        online_det_tlwhs = []
        online_ids = []
        online_det_ids = []
        det_tlwhs = tracker.detections_stracks.tlwh
        det_tlbrs = np.asarray(det_tlwhs).copy()
        det_tlbrs[2:4,:] = det_tlbrs[0:2,:] + det_tlbrs[2:4,:] #tlwh to tlbr
        # det_tlwh = np.asarray(det_tlbrs).copy()
        # det_tlwh[2:4,:] = det_tlwh[2:4,:] - det_tlwh[0:2,:]  # tlbr to tlwh
        # det_tlwh_df = pd.DataFrame(det_tlwh)
        # det_tlwh_df = det_tlwh_df.T
        # det_tlwhs = det_tlwh_df.to_numpy()
        # det_tlwhs = np.array(zip(det_tlwh[0],det_tlwh[1],det_tlwh[2],det_tlwh[3]))
        # online_det_tlwhs.append(det_tlwhs)
        # det_id = np.arange(det_tlwh_df.shape[0])

        for t in online_targets:
            tlwh = t.tlwh

            tid = t.track_id
            vertical = tlwh[2] / tlwh[3] > 1.6
            if tlwh[2] * tlwh[3] > opt.min_box_area and not vertical:
                online_tlwhs.append(tlwh)
                online_ids.append(tid)
        timer.toc()

        detection_tlwhs = []
        detection_ids = []
        for d in tracker.detections_stracks:
            tlwh = d.tlwh
            vertical = tlwh[2] / tlwh[3] > 1.6
            if tlwh[2] * tlwh[3] > opt.min_box_area and not vertical:
                detection_tlwhs.append(tlwh)
                detection_ids.append(-1)
        # save results
        results.append((frame_id + 1, online_tlwhs, online_ids))
        # online_det_ids=online_ids[:len(online_det_tlwhs)]
        # results_det.append((frame_id + 1, online_det_tlwhs, online_det_ids))
        detection_results.append((frame_id + 1, detection_tlwhs, detection_ids))

        if show_image or save_dir is not None:
            online_im = vis.plot_tracking(img0, online_tlwhs, online_ids, frame_id=frame_id,
                                          fps=1. / timer.average_time)
            # draw detections
            '''Detections is list of (x1, y1, x2, y2, object_conf, class_score, class_pred)'''

            if len(tracker.detections_stracks) > 5:
                # det_score = tracker.detections_stracks[5]
                det_score = 1
            else:
                det_score = None
            online_im_det = vis.plot_detections(online_im, det_tlbrs, det_score)
        if show_image:
            cv2.imshow('online_im', online_im_det)
        if save_dir is not None:
            cv2.imwrite(os.path.join(save_dir, '{:05d}.jpg'.format(frame_id)), online_im_det)
        frame_id += 1
    # save results
    write_results(result_filename, results, data_type)
    # save detections
    write_detection_results(result_filename, detection_results, data_type)

    return frame_id, timer.average_time, timer.calls


def main(opt, data_root='/data/MOT16/train', det_root=None, seqs=('MOT16-05',), exp_name='demo', 
         save_images=False, save_videos=False, show_image=True):
    logger.setLevel(logging.INFO)
    today = str(datetime.date.today())
    result_root = os.path.join(data_root, '..', 'results'+today, exp_name)
    mkdir_if_missing(result_root)
    data_type = 'mot'

    # Read config
    cfg_dict = parse_model_cfg(opt.cfg)
    opt.img_size = [int(cfg_dict[0]['width']), int(cfg_dict[0]['height'])]

    # run tracking
    accs = []
    n_frame = 0
    timer_avgs, timer_calls = [], []
    for seq in seqs:
        output_dir = os.path.join(data_root, '..','outputs_with_detections', exp_name, seq) if save_images or save_videos else None

        logger.info('start seq: {}'.format(seq))
        dataloader = datasets.LoadImages(osp.join(data_root, seq, 'img1'), opt.img_size)
        result_filename = os.path.join(result_root, '{}.txt'.format(seq))
        # results_det_filename = os.path.join(result_root, '{}_detections.txt'.format(seq))
        meta_info = open(os.path.join(data_root, seq, 'seqinfo.ini')).read() 
        frame_rate = int(meta_info[meta_info.find('frameRate')+10:meta_info.find('\nseqLength')])
        nf, ta, tc = eval_seq(opt, dataloader, data_type, result_filename,
                              save_dir=output_dir, show_image=show_image, frame_rate=frame_rate)
        n_frame += nf
        timer_avgs.append(ta)
        timer_calls.append(tc)

        # eval
        logger.info('Evaluate seq: {}'.format(seq))
        evaluator = Evaluator(data_root, seq, data_type)
        accs.append(evaluator.eval_file(result_filename))
        if save_videos:
            output_video_path = osp.join(output_dir, '{}.mp4'.format(seq))
            cmd_str = 'ffmpeg -f image2 -i {}/%05d.jpg -c:v copy {}'.format(output_dir, output_video_path)
            os.system(cmd_str)
    timer_avgs = np.asarray(timer_avgs)
    timer_calls = np.asarray(timer_calls)
    all_time = np.dot(timer_avgs, timer_calls)
    avg_time = all_time / np.sum(timer_calls)
    logger.info('Time elapsed: {:.2f} seconds, FPS: {:.2f}'.format(all_time, 1.0 / avg_time))

    # get summary
    metrics = mm.metrics.motchallenge_metrics
    mh = mm.metrics.create()
    summary = Evaluator.get_summary(accs, seqs, metrics)
    strsummary = mm.io.render_summary(
        summary,
        formatters=mh.formatters,
        namemap=mm.io.motchallenge_metric_names
    )
    print(strsummary)
    Evaluator.save_summary(summary, os.path.join(result_root, 'summary_{}.xlsx'.format(exp_name)))



if __name__ == '__main__':
    parser = argparse.ArgumentParser(prog='track.py')
    parser.add_argument('--cfg', type=str, default='cfg/yolov3.cfg', help='cfg file path')
    parser.add_argument('--weights', type=str, default='weights/latest.pt', help='path to weights file')
    parser.add_argument('--iou-thres', type=float, default=0.5, help='iou threshold required to qualify as detected')
    parser.add_argument('--conf-thres', type=float, default=0.5, help='object confidence threshold')
    parser.add_argument('--nms-thres', type=float, default=0.4, help='iou threshold for non-maximum suppression')
    parser.add_argument('--min-box-area', type=float, default=200, help='filter out tiny boxes')
    parser.add_argument('--track-buffer', type=int, default=30, help='tracking buffer')
    parser.add_argument('--test-mot16', action='store_true', help='tracking buffer')
    parser.add_argument('--mot15', action='store_true', help='tracking buffer')
    parser.add_argument('--mot17', action='store_true', help='tracking buffer')
    parser.add_argument('--mot20', action='store_true', help='tracking buffer')
    parser.add_argument('--save-images', action='store_true', help='save tracking results (image)')
    parser.add_argument('--save-videos', action='store_true', help='save tracking results (video)')
    opt = parser.parse_args()
    print(opt, end='\n\n')

    # Added options for different datasets
    if not (opt.test_mot16 or opt.mot15 or opt.mot17 or opt.mot20):
        seqs_str = '''MOT16-02
                      MOT16-04
                      MOT16-05
                      MOT16-09
                      MOT16-10
                      MOT16-11
                      MOT16-13
                    '''
        # seqs_str = '''MOT16-02'''  # temp for debugging. TODO: restore dataset to the full list
        data_root = '/content/drive/MyDrive/MOT-TAU/DATASET/MOT16/train'
    if opt.mot15:
        seqs_str = '''Venice-2
                    KITTI-17
                    KITTI-13
                    ADL-Rundle-8
                    ADL-Rundle-6
                    ETH-Pedcross2
                    ETH-Sunnyday
                    ETH-Bahnhof
                    PETS09-S2L1
                    TUD-Campus
                    TUD-Stadtmitte
        '''
        data_root = '/content/drive/MyDrive/MOT-TAU/DATASET/MOT15/train'
    if opt.mot17:
        seqs_str = '''MOT17-13-SDP
                    MOT17-11-SDP
                    MOT17-10-SDP
                    MOT17-09-SDP
                    MOT17-05-SDP
                    MOT17-04-SDP
                    MOT17-02-SDP
                    MOT17-13-FRCNN
                    MOT17-11-FRCNN
                    MOT17-10-FRCNN
                    MOT17-09-FRCNN
                    MOT17-05-FRCNN
                    MOT17-04-FRCNN
                    MOT17-02-FRCNN
                    MOT17-13-DPM
                    MOT17-11-DPM
                    MOT17-10-DPM
                    MOT17-09-DPM
                    MOT17-05-DPM
                    MOT17-04-DPM
                    MOT17-02-DPM
        '''
        data_root = '/content/drive/MyDrive/MOT-TAU/DATASET/MOT17/train'
    if opt.mot20:
        seqs_str = '''MOT20-05
                    MOT20-03
                    MOT20-02
                    MOT20-01
        '''
        data_root = '/content/drive/MyDrive/MOT-TAU/DATASET/MOT20/train'
    if opt.test_mot16:
        seqs_str = '''MOT16-01
                     MOT16-03
                     MOT16-06
                     MOT16-07
                     MOT16-08
                     MOT16-12
                     MOT16-14'''
        data_root = '/content/drive/MyDrive/MOT-TAU/Amit/dataset/MOT16/test'
    seqs = [seq.strip() for seq in seqs_str.split()]

    main(opt,
         data_root=data_root,
         seqs=seqs,
         exp_name=opt.weights.split('/')[-2],
         show_image=False,
         save_images=opt.save_images, 
         save_videos=opt.save_videos)

