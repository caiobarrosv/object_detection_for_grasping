import os
import numpy as np
import mxnet as mx
import gluoncv as gcv
from mxnet import gluon
# from gluoncv.data.transforms.presets import ssd, rcnn
from gluoncv.model_zoo import get_model
from gluoncv.utils import viz
from gluoncv import data as gdata
import gluoncv.data.transforms.image as timage
import gluoncv.data.transforms.bbox as tbbox
import cv2
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..')))
import utils.common as dataset_commons
import time
import glob
from matplotlib import pyplot as plt
from gluoncv.utils.metrics.voc_detection import VOC07MApMetric
from gluoncv.data.batchify import Tuple, Stack, Pad
from gluoncv.utils.bbox import bbox_iou 
from gluoncv.data.transforms.presets.ssd import SSDDefaultValTransform

data_common = dataset_commons.get_dataset_files()

class Detector:
    def __init__(self, model_path, model='ssd300', ctx='gpu', threshold=0.5, device_id=1, validation_threshold=0.5, batch_size=4, num_workers=2, nms_threshold=0.5):
        self.model_path = os.path.join(data_common['checkpoint_folder'], model_path)
        self.threshold = threshold
        self.device_id = device_id
        self.validation_threshold = validation_threshold
        self.batch_size = batch_size
        self.num_workers = num_workers
        self.nms_threshold = nms_threshold
        
        classes_keys = [key for key in data_common['classes']]
        self.classes = classes_keys
        
        if ctx == 'cpu':
            self.ctx = [mx.cpu()]
        elif ctx == 'gpu':
            self.ctx = [mx.gpu(0)]
        else:
            raise ValueError('Invalid context.')
        
        self.width, self.height = dataset_commons.get_model_prop(model)
        self.model_name = model

        net = get_model(model_name, pretrained=False, ctx=self.ctx)
        # net.set_nms(nms_thresh=0.5, nms_topk=2)
        net.hybridize(static_alloc=True, static_shape=True)
        net.initialize(force_reinit=True, ctx=self.ctx)
        print(self.classes)
        net.reset_class(classes=self.classes)
        net.load_parameters(self.model_path, ctx=self.ctx)
		
        self.net = net

        # TODO: load the train and val rec file
        self.val_file = data_common['record_val_path']

        self.val_dataset = gdata.RecordFileDetection(self.val_file)
        self.val_metric = VOC07MApMetric(iou_thresh=validation_threshold, class_names=self.net.classes)
        
        # Val verdadeiro
        val_batchify_fn = Tuple(Stack(), Pad(pad_val=-1))
        val_loader = gluon.data.DataLoader(
            self.val_dataset.transform(SSDDefaultValTransform(self.width, self.height)),
            batch_size, False, batchify_fn=val_batchify_fn, last_batch='keep', num_workers=self.num_workers)
        self.val_loader = val_loader
    
    def filter_predictions(self, bounding_boxes, scores, class_IDs):
        threshold = self.threshold
        idx = scores.squeeze().asnumpy() > threshold
        fscores = scores.squeeze().asnumpy()[idx]
        fids = class_IDs.squeeze().asnumpy()[idx]
        fbboxes = bounding_boxes.squeeze().asnumpy()[idx]
        return fbboxes, fscores, fids 

    def show_images(self, data, gt_bbox, det_bbox, index):
        # Function still unused but kept for backup
        xmin_gt, ymin_gt, xmax_gt, ymax_gt = [int(x) for x in gt_bbox[0]]
        xmin_pred, ymin_pred, xmax_pred, ymax_pred = [int(x) for x in det_bbox[0]]
        img = data[index]
        img = img.transpose((1, 2, 0))  # Move channel to the last dimension
        # img = img.asnumpy().astype('uint8') # convert to numpy array
        # img = img.astype(np.uint8)  # use uint8 (0-255)
        img = img.asnumpy()
        img = img.astype(np.uint8)
        img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR) # OpenCV uses BGR orde
        cv2.rectangle(img, (xmin_gt, ymin_gt), (xmax_gt, ymax_gt), (255, 0, 0), 1)
        cv2.rectangle(img, (xmin_pred, ymin_pred), (xmax_pred, ymax_pred), (0, 255, 0), 1)
        cv2.startWindowThread()
        cv2.imshow('img', img)
        cv2.waitKey(5000)
        cv2.destroyWindow('img')

    def validate(self):
        """Test on validation dataset."""
        val_data = self.val_loader
        ctx = self.ctx
        val_metric = self.val_metric
        nms_threshold = self.nms_threshold
        validation_threshold = self.validation_threshold

        val_metric.reset()
        # set nms threshold and topk constraint
        # post_nms = maximum number of objects per image
        self.net.set_nms(nms_thresh=nms_threshold, nms_topk=200, post_nms=len(self.classes)) # default: iou=0.45 e topk=400

        # allow the MXNet engine to perform graph optimization for best performance.
        self.net.hybridize(static_alloc=True, static_shape=True)

        # total number of correct prediction by class
        tp = [0] * len(self.classes)
        # count the number of gt by class
        gt_by_class = [0] * len(self.classes)
        # false positives by class
        fp = [0] * len(self.classes)
        # rec and prec by class
        rec_by_class = [0] * len(self.classes)
        prec_by_class = [0] * len(self.classes)

        for batch in val_data:
            batch_size = batch[0].shape[0]
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
            val_metric.update(det_bboxes, det_ids, det_scores, gt_bboxes, gt_ids) #, gt_difficults)

            # Get Micro Averaging (precision and recall by each class) in each batch
            for img in range(batch_size):
                gt_ids_teste, gt_bboxes_teste = [], []
                for ids in det_ids[0][img]:
                    det_ids_number = (int(ids.asnumpy()[0]))
                    # It is required to check if the predicted class is in the image
                    # otherwise, count it as a false positive and do not include in the list
                    if det_ids_number in list(gt_ids[0][img]):
                        gt_index = list(gt_ids[0][img]).index(det_ids_number)
                        gt_ids_teste.extend(gt_ids[0][img][gt_index])
                        gt_bboxes_teste.append(gt_bboxes[0][img][gt_index])
                    else:
                        fp[det_ids_number] += 1  # Wrong classification

                xww = 1
                
                # count +1 for this class id. It will get the total number of gt by class
                # It is useful when considering unbalanced datasets
                for gt_idx in gt_ids[0][img]:
                    index = int(gt_idx.asnumpy()[0])
                    gt_by_class[index] += 1
                
                for ids in range(len(gt_bboxes_teste)):
                    det_bbox_ids = det_bboxes[0][img][ids]
                    det_bbox_ids = det_bbox_ids.asnumpy()
                    det_bbox_ids = np.expand_dims(det_bbox_ids, axis=0)
                    predict_ind = int(det_ids[0][img][ids].asnumpy()[0])
                    
                    gt_bbox_ids = gt_bboxes_teste[ids]
                    gt_bbox_ids = gt_bbox_ids.asnumpy()
                    gt_bbox_ids = np.expand_dims(gt_bbox_ids, axis=0)
                    gt_ind = int(gt_ids_teste[ids].asnumpy()[0])
                    
                    iou = bbox_iou(det_bbox_ids, gt_bbox_ids)

                    # Uncomment the following line if you want to plot the images in each inference to visually  check the tp, fp and fn 
                    # self.show_images(x, gt_bbox_ids, det_bbox_ids, img)
                    
                    # Check if IoU is above the threshold and the class id corresponds to the ground truth
                    if (iou > validation_threshold) and (predict_ind == gt_ind):
                        tp[gt_ind] += 1 # Correct classification
                    else:
                        fp[predict_ind] += 1  # Wrong classification
        
        # calculate the Recall and Precision by class
        tp = np.array(tp)
        fp = np.array(fp)
        # rec and prec according to the micro averaging
        for i, (gt_value, tp_value) in enumerate(zip(gt_by_class, tp)):
            rec_by_class[i] += tp_value/gt_value

            # If an element of fp + tp is 0,
            # the corresponding element of prec[l] is nan.
            with np.errstate(divide='ignore', invalid='ignore'):
                prec_by_class[i] += tp_value/(tp_value+fp[i])

        rec, prec = val_metric._recall_prec()
        return val_metric.get(), rec_by_class, prec_by_class
    
    def detect(self, image, plot=False):
        image_tensor, image = gcv.data.transforms.presets.ssd.load_test(image, self.width)
        labels, scores, bboxes = self.net(image_tensor.as_in_context(self.ctx))
        if plot:
            ax = viz.plot_bbox(image, bboxes[0], scores[0], labels[0], class_names=self.net.classes)
            plt.show()

    def detect_webcam_video(self, video_font):
        # Load the webcam handler
        cap = cv2.VideoCapture(video_font) # 1 for droid-cam
        time.sleep(1) ### letting the camera autofocus

        axes = None
        a = cv2.waitKey(0) # close window when ESC is pressed     
        while a is not 27:
            # Load frame from the camera
            ret, frame = cap.read()

            # Image pre-processing
            frame = mx.nd.array(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).astype('uint8')
            rgb_nd, frame = gcv.data.transforms.presets.ssd.transform_test(frame, short=self.width, max_size=700)

            # Run frame through network
            class_IDs, scores, bounding_boxes = self.net(rgb_nd.as_in_context(self.ctx))          

            fbounding_boxes, fscores, fclass_IDs = self.filter_predictions(bounding_boxes, scores, class_IDs)
            gcv.utils.viz.cv_plot_image(frame)
            if fclass_IDs.size > 0:
                # Display the result
                img = gcv.utils.viz.cv_plot_bbox(frame, fbounding_boxes, fscores, fclass_IDs, class_names=self.net.classes)
                gcv.utils.viz.cv_plot_image(img)
            
            a = cv2.waitKey(1) # close window when ESC is pressed            
        
        cap.release()
        cv2.destroyAllWindows()

def main():
    # TODO: You just need to pass the param name inside the log folder (checkpoints folder configured in config.json)
    params = 'ssd_300_vgg16_atrous_voc_best_epoch_0025_map_0.8749.params'

    det = Detector(params, model='ssd300_vgg16_voc', ctx='gpu', threshold=0.1, device_id=1, batch_size=4, num_workers=2, nms_threshold=0.5)

    print("\nPlease configure the video/images files path in config.json before running the next command.")    

    a = int(input("Choose an option: \n[1] - Perform testing in images \n[2] - Perform testing in videos \n[3] - Perform testing using webcam\
        \n[4] - Perform only validation using a pre-trained network and a .rec val file\nOption: "))
    
    if a == 1:
        images = glob.glob(data_common['image_folder'] + "/" + "*.jpg")
        for image in images:
            det.detect(image, plot=True)
    elif a == 2:
        file_name = str(input("Write the video file name with the extension (!!) that is inside the video folder configured in the config.json file: "))
        file_name = glob.glob(data_common['video_folder'] + "/" + file_name)[0]
        det.detect_webcam_video(file_name)
    elif a == 3:
        device_id = int(input("Choose the device id to connect (default: 0): "))
        det.detect_webcam_video(device_id)
    elif a == 4:
        input("Please configure the val.rec file path in the config.json. Press enter to continue.")
        det.validate()
    else:
        print("Please choose the right option")

if __name__ == "__main__":
    main()