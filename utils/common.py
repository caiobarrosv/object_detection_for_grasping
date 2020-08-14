import os
import json

def get_dataset_files():
    json_path = os.path.abspath(os.path.join(os.path.dirname( __file__ ), '..', 'config_files/config.json'))

    with open(json_path, "rb") as file:
        config = json.loads(file.read())
    
    dir_ = {
        'classes' : config["classes"],
        'checkpoint_folder' : config["checkpoint_folder"],
        'logs_folder' : config["logs_folder"],
        'image_folder' : config["image_folder"],
        'image_val_folder' : config["image_val_folder"],
        'video_folder' : config["video_folder"],
        'csv_train' : config["csv_train"],
        'csv_validation' : config["csv_validation"],
        'lst_train_path' : config["lst_train_path"],
        'lst_val_path' : config["lst_val_path"],
        'record_train_path' : config["record_train_path"],
        'record_val_path' : config["record_val_path"]
    }    

    return dir_

def get_model_prop(model_name):
    if model_name.lower() == 'ssd_300_vgg16_atrous_voc':
        width, height = 300, 300
    elif model_name.lower() == 'ssd_512_resnet50_v1_voc':
        width, height = 512, 512
    elif model_name.lower() == 'ssd_512_vgg16_atrous_voc':
        width, height = 512, 512
    elif model_name.lower() == 'ssd_300_vgg16_atrous_coco':
        width, height = 300, 300
    elif model_name.lower() == 'ssd_512_vgg16_atrous_coco':
        width, height = 512, 512
    elif model_name.lower() == 'ssd_512_resnet50_v1_coco':
        width, height = 512, 512
    else:
        raise ValueError('Invalid model `{}`.'.format(model_name.lower()))
    return width, height