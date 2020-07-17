"""Train SSD"""
import argparse
import os
import logging
import time
import numpy as np
import mxnet as mx
from mxnet import nd
from mxnet import gluon
from mxnet import autograd
import gluoncv as gcv
from gluoncv import data as gdata
from gluoncv import utils as gutils
from gluoncv.model_zoo import get_model
from gluoncv.data.batchify import Tuple, Stack, Pad
from gluoncv.data.transforms.presets.ssd import SSDDefaultTrainTransform
from gluoncv.data.transforms.presets.ssd import SSDDefaultValTransform
from gluoncv.utils.metrics.voc_detection import VOC07MApMetric
from gluoncv.utils.metrics.coco_detection import COCODetectionMetric
from gluoncv.utils.metrics.accuracy import Accuracy

import os


'''
The models used in this project:
    PASCAL VOC:
        1) ssd_300_vgg16_atrous_voc 
        2) ssd_512_vgg16_atrous_voc 
        3) ssd_512_resnet50_v1_voc
        4) faster_rcnn_resnet50_v1b_voc
        5) yolo3_darknet53_voc 
    COCO:
        1) ssd_300_vgg16_atrous_coco 
        2) ssd_512_vgg16_atrous_coco 
        3) ssd_512_resnet50_v1_coco 
        4) faster_rcnn_resnet50_v1b_coco 
        5) yolo3_darknet53_coco 
'''

save_prefix = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'checkpoints/'))

def parse_args():
    parser = argparse.ArgumentParser(description='Train SSD networks.')
    parser.add_argument('--network', type=str, default='vgg16_atrous',
                        help="Base network name which serves as feature extraction base.")
    parser.add_argument('--data-shape', type=int, default=300,
                        help="Input data shape, use 300, 512.")
    parser.add_argument('--batch-size', type=int, default=4,
                        help='Training mini-batch size')
    parser.add_argument('--dataset', type=str, default='voc',
                        help='Training dataset. Now support voc.')
    parser.add_argument('--num-workers', '-j', dest='num_workers', type=int,
                        default=2, help='Number of data workers, you can use larger '
                        'number to accelerate data loading, if you CPU and GPUs are powerful.')
    parser.add_argument('--epochs', type=int, default=2,
                        help='Training epochs.')
    parser.add_argument('--resume', type=str, default='',
                        help='Resume from previously saved parameters if not None. '
                        'For example, you can resume from ./ssd_xxx_0123.params')
    parser.add_argument('--start-epoch', type=int, default=0,
                        help='Starting epoch for resuming, default is 0 for new training.'
                        'You can specify it to 100 for example to start from 100 epoch.')
    parser.add_argument('--lr', type=float, default=0.001,
                        help='Learning rate, default is 0.001')
    parser.add_argument('--lr-decay', type=float, default=0.1,
                        help='decay rate of learning rate. default is 0.1.')
    parser.add_argument('--lr-decay-epoch', type=str, default='60, 80',
                        help='epoches at which learning rate decays. default is 60, 80.')
    parser.add_argument('--momentum', type=float, default=0.9,
                        help='SGD momentum, default is 0.9')
    parser.add_argument('--wd', type=float, default=0.0005,
                        help='Weight decay, default is 5e-4')
    parser.add_argument('--log-interval', type=int, default=20,
                        help='Logging mini-batch interval. Default is 100.')
    parser.add_argument('--save-interval', type=int, default=0,
                        help='Saving parameters epoch interval, best model will always be saved.')
    parser.add_argument('--val-interval', type=int, default=1,
                        help='Epoch interval for validation, increase the number will reduce the '
                             'training time if validation is slow.')
    args = parser.parse_args()
    return args

def get_dataset(dataset, args):
    if dataset.lower() == 'voc':
        pikachu_train = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'data/pikachu_train.rec'))
        train_dataset = gdata.RecordFileDetection(pikachu_train)
        val_dataset = gdata.RecordFileDetection(pikachu_train)
        classes = ['pikachu']
        val_metric = VOC07MApMetric(iou_thresh=0.5, class_names=classes)
        # train_dataset = gdata.VOCDetection(
        #     splits=[(2007, 'trainval'), (2012, 'trainval')])
        # val_dataset = gdata.VOCDetection(
        #     splits=[(2007, 'test')])
        # val_metric = VOC07MApMetric(iou_thresh=0.5, class_names=val_dataset.classes)
    elif dataset.lower() == 'coco':
        train_dataset = gdata.COCODetection(splits='instances_train2017')
        val_dataset = gdata.COCODetection(splits='instances_val2017', skip_empty=False)
        val_metric = COCODetectionMetric(
            val_dataset, save_prefix + '_eval', cleanup=True,
            data_shape=(args.data_shape, args.data_shape))
        # coco validation is slow, consider increase the validation interval
        if args.val_interval == 1:
            args.val_interval = 10
    else:
        raise NotImplementedError('Dataset: {} not implemented.'.format(dataset))
    return train_dataset, val_dataset, val_metric

def get_dataloader(net, train_dataset, val_dataset):
    """
    Get dataloader.

    Exatamente igual ao outro, exceto pelo val_loader
    """
    data_shape, batch_size, num_workers = args.data_shape, args.batch_size, args.num_workers
    width, height = data_shape, data_shape
    
    # use fake data to generate fixed anchors for target generation
    with autograd.train_mode():
        _, _, anchors = net(mx.nd.zeros((1, 3, height, width)))
    
    batchify_fn = Tuple(Stack(), Stack(), Stack())  # stack image, cls_targets, box_targets
    train_loader = gluon.data.DataLoader(
        train_dataset.transform(SSDDefaultTrainTransform(width, height, anchors)),
        batch_size, True, batchify_fn=batchify_fn, last_batch='rollover', num_workers=num_workers)
    
    val_batchify_fn = Tuple(Stack(), Pad(pad_val=-1))
    val_loader = gluon.data.DataLoader(
        val_dataset.transform(SSDDefaultValTransform(width, height)),
        batch_size, False, batchify_fn=val_batchify_fn, last_batch='keep', num_workers=num_workers)
    
    return train_loader, val_loader

def save_params(net, best_map, current_map, epoch, save_interval):
    current_map = float(current_map)
    if current_map > best_map[0]:
        best_map[0] = current_map
        net.save_params('{:s}_best.params'.format(prefix, epoch, current_map))
        with open(prefix+'_best_map.log', 'a') as f:
            f.write('\n{:04d}:\t{:.4f}'.format(epoch, current_map))
    if save_interval and epoch % save_interval == 0:
        net.save_params('{:s}_{:04d}_{:.4f}.params'.format(prefix, epoch, current_map))

def validate(net, val_data, ctx, eval_metric):
    """Test on validation dataset."""
    eval_metric.reset()
    # set nms threshold and topk constraint
    net.set_nms(nms_thresh=0.45, nms_topk=400)
    net.hybridize()
    for batch in val_data:
        data = gluon.utils.split_and_load(batch[0], ctx_list=ctx, batch_axis=0, even_split=False)
        label = gluon.utils.split_and_load(batch[1], ctx_list=ctx, batch_axis=0, even_split=False)
        det_bboxes = []
        det_ids = []
        det_scores = []
        gt_bboxes = []
        gt_ids = []
        gt_difficults = []
        for x, y in zip(data, label):
            # get prediction results
            ids, scores, bboxes = net(x)
            det_ids.append(ids)
            det_scores.append(scores)
            # clip to image size
            det_bboxes.append(bboxes.clip(0, batch[0].shape[2]))
            # split ground truths
            gt_ids.append(y.slice_axis(axis=-1, begin=4, end=5))
            gt_bboxes.append(y.slice_axis(axis=-1, begin=0, end=4))
            gt_difficults.append(y.slice_axis(axis=-1, begin=5, end=6) if y.shape[-1] > 5 else None)

        # update metric
        eval_metric.update(det_bboxes, det_ids, det_scores, gt_bboxes, gt_ids, gt_difficults)
    return eval_metric.get()

def train(net, train_data, val_data, eval_metric, ctx, args):
    """Training pipeline"""

    # Returns a Dict containing this Block and all of its children’s Parameters
    # For example, collect the specified parameters in [‘conv1.weight’, ‘conv1.bias’, ‘fc.weight’, ‘fc.bias’]:
    # reset_ctx Re-assign all Parameters to other contexts.
    net.collect_params().reset_ctx(ctx)
    
    # wd: The weight decay (or L2 regularization) coefficient.
    trainer = gluon.Trainer(
        net.collect_params(), 'sgd',
        {'learning_rate': args.lr, 'wd': args.wd, 'momentum': args.momentum})

    # lr decay policy
    lr_decay = float(args.lr_decay)
    lr_steps = sorted([float(ls) for ls in args.lr_decay_epoch.split(',') if ls.strip()])

    mbox_loss = gcv.loss.SSDMultiBoxLoss()
    ce_metric = mx.metric.Loss('CrossEntropy')
    smoothl1_metric = mx.metric.Loss('SmoothL1')

    # set up logger
    logging.basicConfig()
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    log_file_path = save_prefix + '_train.log'
    log_dir = os.path.dirname(log_file_path)
    if log_dir and not os.path.exists(log_dir):
        os.makedirs(log_dir)
    fh = logging.FileHandler(log_file_path)
    logger.addHandler(fh)
    logger.info(args)
    logger.info('Start training from [Epoch {}]'.format(args.start_epoch))
    
    best_map = [0]
    start_train_time = time.time()

    for epoch in range(args.start_epoch, args.epochs):
        start_epoch_time = time.time()

        while lr_steps and epoch >= lr_steps[0]:
            new_lr = trainer.learning_rate * lr_decay
            lr_steps.pop(0) # removes the first element in the list
            trainer.set_learning_rate(new_lr) # Set a new learning rate
            logger.info("[Epoch {}] Set learning rate to {}".format(epoch, new_lr))
        
        ce_metric.reset() # Resets the internal evaluation result to initial state.
        smoothl1_metric.reset() # Resets the internal evaluation result to initial state.
        tic = time.time()
        btic = time.time()
        net.hybridize(static_alloc=True, static_shape=True) # Activates or deactivates HybridBlocks recursively. it speeds up the training process
        for i, batch in enumerate(train_data):
            batch_size = batch[0].shape[0]
            data = gluon.utils.split_and_load(batch[0], ctx_list=ctx, batch_axis=0)
            cls_targets = gluon.utils.split_and_load(batch[1], ctx_list=ctx, batch_axis=0)
            box_targets = gluon.utils.split_and_load(batch[2], ctx_list=ctx, batch_axis=0)
            with autograd.record():
                cls_preds = []
                box_preds = []
                for x in data:
                    cls_pred, box_pred, _ = net(x)
                    cls_preds.append(cls_pred)
                    box_preds.append(box_pred)
                sum_loss, cls_loss, box_loss = mbox_loss(
                    cls_preds, box_preds, cls_targets, box_targets)
                autograd.backward(sum_loss)
            # since we have already normalized the loss, we don't want to normalize
            # by batch-size anymore
            trainer.step(1)
            ce_metric.update(0, [l * batch_size for l in cls_loss])
            smoothl1_metric.update(0, [l * batch_size for l in box_loss])
            if args.log_interval and not (i + 1) % args.log_interval:
                name1, loss1 = ce_metric.get()
                name2, loss2 = smoothl1_metric.get()
                logger.info('[Epoch {}][Batch {}], Speed: {:.3f} samples/sec, {}={:.3f}, {}={:.3f}'.format(
                    epoch, i, batch_size/(time.time()-btic), name1, loss1, name2, loss2))
            btic = time.time()

        name1, loss1 = ce_metric.get()
        name2, loss2 = smoothl1_metric.get()
        logger.info('[Epoch {}] Training cost: {:.3f}, {}={:.3f}, {}={:.3f}'.format(
            epoch, (time.time()-tic), name1, loss1, name2, loss2))

        if not (epoch + 1) % args.val_interval:
            # consider reduce the frequency of validation to save time
            map_name, mean_ap = validate(net, val_data, ctx, eval_metric)
            val_msg = '\n'.join(['{}={}'.format(k, v) for k, v in zip(map_name, mean_ap)])
            logger.info('[Epoch {}] Validation: \n{}'.format(epoch, val_msg))
            current_map = float(mean_ap[-1])
        else:
            current_map = 0.

        save_params(net, best_map, current_map, epoch, args.save_interval)
        
        # Displays the time spent in each epoch
        end_epoch_time = time.time()
        logger.info('Epoch time {:.3f}'.format(end_epoch_time - start_epoch_time))
        ## Current epoch finishes

    # Displays the total time of the training
    end_train_time = time.time()
    logger.info('Train time {:.3f}'.format(end_train_time - start_train_time))

if __name__ == '__main__':
    args = parse_args()

    # fix seed for mxnet, numpy and python builtin random generator.
    gutils.random.seed(233)

    try:
        a = mx.nd.zeros((1,), ctx=mx.gpu(0))
        ctx = [mx.gpu(0)]
    except:
        ctx = [mx.cpu()]
        print('Could not load on gpu. Loading on CPU instead')

    # Load the network
    net_name = '_'.join(('ssd', str(args.data_shape), args.network, args.dataset))
    save_prefix += net_name

    # pretrained or pretrained_base?
    # pretrained (bool or str) – Boolean value controls whether to load the default 
    # pretrained weights for model. String value represents the hashtag for a certain 
    # version of pretrained weights.
    # pretrained_base (bool or str, optional, default is True) – Load pretrained base 
    # network, the extra layers are randomized. Note that if pretrained is True, this
    #  has no effect.
    net = get_model(net_name, pretrained=True, norm_layer=gluon.nn.BatchNorm)

    classes = ['pikachu']  # only one foreground class here
    net.reset_class(classes)

    if args.resume.strip():
        net.initialize(force_reinit=True)
        net.load_params(args.resume.strip())
    else:
        for param in net.collect_params().values():
            if param._data is not None:
                continue
            param.initialize()

    # training data
    train_dataset, val_dataset, eval_metric = get_dataset(args.dataset, args)
    train_data, val_data = get_dataloader(net, train_dataset, val_dataset)

    # training
    train(net, train_data, val_data, eval_metric, ctx, args)