"""Train SSD"""
# import argparse
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

from mxnet.contrib import amp

import os
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..')))
import utils.dataset_commons as dataset_commons

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

class training_network():
    def __init__(self, model='ssd300', ctx='gpu', resume_training=False, batch_size=4, num_workers=2, lr=0.001, 
                 lr_decay=0.1, lr_decay_epoch='60, 80', wd=0.0005, momentum=0.9, val_interval=1, start_epoch=0,
                 epochs=2, dataset='voc', network='vgg16_atrous', save_interval=0, log_interval=20, resume=''):
        """
        Script responsible for training the class

        Arguments:
            model (str): One of the following models [ssd_300_vgg16_atrous_voc]
            val_interval (int, default: 1): Epoch interval for validation, increase the number will reduce the
                training time if validation is slow.
            save_interval (int, default: 0): Saving parameters epoch interval, best model will always be saved.
            log_interval (int, default: 20): Logging mini-batch interval. Default is 100.
            wd (float, default: 0.0005): Weight decay, default is 5e-4
            momentum (float, default:0.9): SGD momentum, default is 0.9
            lr_decay_epoch (str, default: '60, 80'): epoches at which learning rate decays. default is 60, 80.
            lr_decay (float, default: 0.1): decay rate of learning rate. default is 0.1.
            lr (float, default: 0.001): Learning rate, default is 0.001
            start_epoch (int, default: 0): Starting epoch for resuming, default is 0 for new training. You can
                specify it to 100 for example to start from 100 epoch.
            resume (str, default: ''): Resume from previously saved parameters if not None. For example, you 
                can resume from ./ssd_xxx_0123.params'
            epochs (int, default:2): Training epochs.
            num_worker (int, default: 2): number to accelerate data loading, if you CPU and GPUs are powerful.
            dataset (str, default:'voc'): Training dataset. Now support voc.
            batch_size (int, default: 4): Training mini-batch size
            data_shape (int, default: 300): Input data shape, use 300, 512.
            network (str, default:'vgg16_atrous'): Base network name which serves as feature extraction base.
        """
        data_common = dataset_commons.get_dataset_files()

        amp.init()

        # TRAINING PARAMETERS
        self.resume_training = resume_training
        self.batch_size=batch_size
        self.num_workers=num_workers
        self.learning_rate = lr
        self.weight_decay = wd
        self.momentum = momentum
        self.optimizer = 'sgd'
        self.lr_decay = lr_decay
        self.lr_decay_epoch = lr_decay_epoch
        self.val_interval = val_interval
        self.start_epoch = start_epoch
        self.epochs = epochs
        self.log_interval = log_interval
        self.save_interval = save_interval
        self.resume = resume

        if ctx == 'cpu':
            self.ctx = [mx.cpu()]
        elif ctx == 'gpu':
            self.ctx = [mx.gpu(0)]
        else:
            raise ValueError('Invalid context.')
            
        # fix seed for mxnet, numpy and python builtin random generator.
        gutils.random.seed(233)

        if model.lower() == 'ssd300':
            self.model_name = 'ssd_300_vgg16_atrous_voc' #'ssd_300_vgg16_atrous_coco'
            self.dataset= 'voc'
            self.width, self.height = 300, 300
            self.network = 'vgg16_atrous'
        
        # Load the network
        self.save_prefix = os.path.join(data_common['checkpoint_folder'], self.model_name)
        
        # train and val rec file
        self.train_file = data_common['record_train_path']
        self.val_file = data_common['record_val_path']

        classes_keys = [key for key in data_common['classes']]
        self.classes = classes_keys
        print(self.classes)

        # pretrained or pretrained_base?
        # pretrained (bool or str) – Boolean value controls whether to load the default 
        # pretrained weights for model. String value represents the hashtag for a certain 
        # version of pretrained weights.
        # pretrained_base (bool or str, optional, default is True) – Load pretrained base 
        # network, the extra layers are randomized. Note that if pretrained is True, this
        # has no effect.
        self.net = get_model(self.model_name, pretrained=True, norm_layer=gluon.nn.BatchNorm)
        self.net.reset_class(self.classes)

    def get_dataset(self):
        if self.dataset == 'voc':
            self.train_dataset = gdata.RecordFileDetection(self.train_file)
            self.val_dataset = gdata.RecordFileDetection(self.val_file)            
            self.val_metric = VOC07MApMetric(iou_thresh=0.5, class_names=self.classes)
            # train_dataset = gdata.VOCDetection(
            #     splits=[(2007, 'trainval'), (2012, 'trainval')])
            # val_dataset = gdata.VOCDetection(
            #     splits=[(2007, 'test')])
            # val_metric = VOC07MApMetric(iou_thresh=0.5, class_names=val_dataset.classes)
        # elif dataset.lower() == 'coco':
        #     self.train_dataset = gdata.COCODetection(splits='instances_train2017')
        #     self.val_dataset = gdata.COCODetection(splits='instances_val2017', skip_empty=False)
        #     self.val_metric = COCODetectionMetric(
        #         val_dataset, save_prefix + '_eval', cleanup=True,
        #         data_shape=(args.data_shape, args.data_shape))
        #     # coco validation is slow, consider increase the validation interval
        #     if args.val_interval == 1:
        #         args.val_interval = 10
        else:
            raise NotImplementedError('Dataset: {} not implemented.'.format(dataset))

    def initialize_network(self):
        if self.resume_training:
            self.net.initialize(force_reinit=True, ctx=self.ctx)
            self.net.load_params(self.resume, ctx=self.ctx)
        else:
            for param in self.net.collect_params().values():
                if param._data is not None:
                    continue
                param.initialize()
    
    def get_dataloader(self):
        batch_size, num_workers = self.batch_size, self.num_workers
        width, height = self.width, self.height
        train_dataset = self.train_dataset
        val_dataset = self.val_dataset
        batch_size = self.batch_size
        num_workers = self.num_workers

        # use fake data to generate fixed anchors for target generation
        with autograd.train_mode():
            _, _, anchors = self.net(mx.nd.zeros((1, 3, height, width)))
        
        batchify_fn = Tuple(Stack(), Stack(), Stack())  # stack image, cls_targets, box_targets
        self.train_loader = gluon.data.DataLoader(
            train_dataset.transform(SSDDefaultTrainTransform(width, height, anchors)),
            batch_size, True, batchify_fn=batchify_fn, last_batch='rollover', num_workers=num_workers)
        
        val_batchify_fn = Tuple(Stack(), Pad(pad_val=-1))
        self.val_loader = gluon.data.DataLoader(
            val_dataset.transform(SSDDefaultValTransform(width, height)),
            batch_size, False, batchify_fn=val_batchify_fn, last_batch='keep', num_workers=num_workers)

    def save_params(self, best_map, current_map, epoch, save_interval):
        prefix = self.save_prefix

        current_map = float(current_map)
        if current_map > best_map[0]:
            best_map[0] = current_map
            self.net.save_params('{:s}_best_epoch_{:04d}.params'.format(prefix, epoch, current_map))
            with open(prefix+'_best_map.log', 'a') as f:
                f.write('\n{:04d}:\t{:.4f}'.format(epoch, current_map))
        if save_interval and epoch % save_interval == 0:
            self.net.save_params('{:s}_{:04d}_{:.4f}.params'.format(prefix, epoch, current_map))

    def validate(self):
        """Test on validation dataset."""
        val_data = self.val_loader
        ctx = self.ctx
        eval_metric = self.val_metric

        eval_metric.reset()
        # set nms threshold and topk constraint
        self.net.set_nms(nms_thresh=0.45, nms_topk=400)
        self.net.hybridize()
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
                ids, scores, bboxes = self.net(x)
                det_ids.append(ids)
                det_scores.append(scores)
                # clip to image size
                det_bboxes.append(bboxes.clip(0, batch[0].shape[2]))
                # split ground truths
                gt_ids.append(y.slice_axis(axis=-1, begin=4, end=5))
                gt_bboxes.append(y.slice_axis(axis=-1, begin=0, end=4))
                # gt_difficults.append(y.slice_axis(axis=-1, begin=5, end=6) if y.shape[-1] > 5 else None)

            # update metric
            eval_metric.update(det_bboxes, det_ids, det_scores, gt_bboxes, gt_ids) #, gt_difficults)
        return eval_metric.get()

    def train(self):
        """Training pipeline"""
        train_data = self.train_loader
        val_data = self.val_loader
        eval_metric = self.val_metric
        ctx = self.ctx
        lr = self.learning_rate
        wd = self.weight_decay
        momentum = self.momentum
        optimizer = self.optimizer
        lr_decay = self.lr_decay
        lr_decay_epoch = self.lr_decay_epoch
        val_interval = self.val_interval
        save_prefix = self.save_prefix
        start_epoch = self.start_epoch
        epochs = self.epochs
        save_interval = self.save_interval
        log_interval = self.log_interval

        # Returns a Dict containing this Block and all of its children’s Parameters
        # For example, collect the specified parameters in [‘conv1.weight’, ‘conv1.bias’, ‘fc.weight’, ‘fc.bias’]:
        # reset_ctx Re-assign all Parameters to other contexts.
        self.net.collect_params().reset_ctx(ctx)
        
        # wd: The weight decay (or L2 regularization) coefficient.
        trainer = gluon.Trainer(self.net.collect_params(), optimizer,
                                {'learning_rate': lr, 'wd': wd, 'momentum': momentum})
        
        amp.init_trainer(trainer)

        # lr decay policy
        lr_decay = float(lr_decay)
        lr_steps = sorted([float(ls) for ls in lr_decay_epoch.split(',') if ls.strip()])

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
        # logger.info(args)
        logger.info('Start training from [Epoch {}]'.format(start_epoch))
        
        best_map = [0]
        start_train_time = time.time()

        for epoch in range(start_epoch, epochs):
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
            
            # Activates or deactivates HybridBlocks recursively. it speeds up the training process
            self.net.hybridize(static_alloc=True, static_shape=True)
            
            for i, batch in enumerate(train_data):
                batch_size = batch[0].shape[0]
                data = gluon.utils.split_and_load(batch[0], ctx_list=ctx, batch_axis=0)
                cls_targets = gluon.utils.split_and_load(batch[1], ctx_list=ctx, batch_axis=0)
                box_targets = gluon.utils.split_and_load(batch[2], ctx_list=ctx, batch_axis=0)
                with autograd.record():
                    cls_preds = []
                    box_preds = []
                    for x in data:
                        cls_pred, box_pred, _ = self.net(x)
                        cls_preds.append(cls_pred)
                        box_preds.append(box_pred)
                    
                    sum_loss, cls_loss, box_loss = mbox_loss(
                        cls_preds, box_preds, cls_targets, box_targets)
                    
                    with amp.scale_loss(sum_loss, trainer) as scaled_loss:
                        autograd.backward(scaled_loss)
                    # autograd.backward(sum_loss)
                
                # since we have already normalized the loss, we don't want to normalize
                # by batch-size anymore
                trainer.step(1)
                ce_metric.update(0, [l * batch_size for l in cls_loss])
                smoothl1_metric.update(0, [l * batch_size for l in box_loss])
                if log_interval and not (i + 1) % log_interval:
                    name1, loss1 = ce_metric.get()
                    name2, loss2 = smoothl1_metric.get()
                    logger.info('[Epoch {}][Batch {}], Speed: {:.3f} samples/sec, {}={:.3f}, {}={:.3f}'.format(
                        epoch, i, batch_size/(time.time()-btic), name1, loss1, name2, loss2))
                btic = time.time()

            name1, loss1 = ce_metric.get()
            name2, loss2 = smoothl1_metric.get()
            logger.info('[Epoch {}] Training cost: {:.3f}, {}={:.3f}, {}={:.3f}'.format(
                epoch, (time.time()-tic), name1, loss1, name2, loss2))

            if not (epoch + 1) % val_interval:
                # consider reduce the frequency of validation to save time
                map_name, mean_ap = self.validate()
                val_msg = '\n'.join(['{}={}'.format(k, v) for k, v in zip(map_name, mean_ap)])
                logger.info('[Epoch {}] Validation: \n{}'.format(epoch, val_msg))
                current_map = float(mean_ap[-1])
            else:
                current_map = 0.

            self.save_params(best_map, current_map, epoch, save_interval)
            
            # Displays the time spent in each epoch
            end_epoch_time = time.time()
            logger.info('Epoch time {:.3f}'.format(end_epoch_time - start_epoch_time))
            ## Current epoch finishes

        # Displays the total time of the training
        end_train_time = time.time()
        logger.info('Train time {:.3f}'.format(end_train_time - start_train_time))

if __name__ == '__main__':
    train_object = training_network(model='ssd300', ctx='gpu', lr=0.001, batch_size=4, epochs=2)

    train_object.get_dataset()

    train_object.initialize_network()
    
    # Loads the dataset according to the batch size and num_workers
    train_object.get_dataloader()

    # training
    train_object.train()