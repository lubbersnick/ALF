###################################
# Step 1: Specify a configuration #
###################################

import os
import json
import pickle
import time
from importlib import import_module
import sys
from pathlib import Path

# Load ASE library
import ase
from ase import Atoms

import parsl
# Check to see if parsl is available
import alframework
#from alframework.parsl_resource_configs.darwin import config_atdm_ml
from alframework.tools.tools import parsl_task_queue
from alframework.tools.tools import store_current_data
from alframework.tools.tools import load_config_file
from alframework.tools.tools import find_empty_directory
from alframework.tools.tools import system_checker
#import logging
#logging.basicConfig(level=logging.DEBUG)

# Load the master configuration:
master_config = load_config_file(sys.argv[1])
if 'master_directory' not in master_config:
    master_config['master_directory'] = None

# Load the builder config:
builder_config = load_config_file(master_config['builder_config_path'],master_config['master_directory'])

# Load the sampler config:
sampler_config = load_config_file(master_config['sampler_config_path'],master_config['master_directory'])

# Load the QM config:
QM_config = load_config_file(master_config['QM_config_path'],master_config['master_directory'])

# Load the ML config:
ML_config = load_config_file(master_config['ML_config_path'],master_config['master_directory'])

#Define queues: 
QM_task_queue = parsl_task_queue()
ML_task_queue = parsl_task_queue()
builder_task_queue = parsl_task_queue()
sampler_task_queue = parsl_task_queue()

module_string = '.'.join(master_config['parsl_configuration'].split('.')[:-1])
class_string = master_config['parsl_configuration'].split('.')[-1]
parsl_configuration = getattr(import_module(module_string),class_string)

# Load the Parsl config
parsl.load(parsl_configuration)

#make needed directories
#This is kinda inflexible, may need to revisit.
tempPath = Path('/'.join(master_config['h5_path'].split('/')[:-1]))
tempPath.mkdir(parents=True,exist_ok=True)

tempPath = Path('/'.join(master_config['model_path'].split('/')[:-1]))
tempPath.mkdir(parents=True,exist_ok=True)

tempPath = Path(sampler_config['meta_dir'])
tempPath.mkdir(parents=True,exist_ok=True)

#############################
# Step 2: Define Parsl tasks#
#############################
#Builder
module_string = '.'.join(master_config['builder_task'].split('.')[:-1])
class_string = master_config['builder_task'].split('.')[-1]
builder_task = getattr(import_module(module_string),class_string)

#Sampler
module_string = '.'.join(master_config['sampler_task'].split('.')[:-1])
class_string = master_config['sampler_task'].split('.')[-1]
sampler_task = getattr(import_module(module_string),class_string)

#QM
module_string = '.'.join(master_config['QM_task'].split('.')[:-1])
class_string = master_config['QM_task'].split('.')[-1]
qm_task = getattr(import_module(module_string),class_string)

#ML
module_string = '.'.join(master_config['ML_task'].split('.')[:-1])
class_string = master_config['ML_task'].split('.')[-1]
ml_task = getattr(import_module(module_string),class_string)

##########################################
## Step 3: Evaluate restart possibilites #
##########################################
if os.path.exists(master_config['status_path']):
    with open(master_config['status_path'],'r') as input_file:
        status = json.load(input_file)
else:
    status = {}
    status['current_training_id'] = find_empty_directory(master_config['model_path'])
    if status['current_training_id'] > 0:
        status['current_model_id'] = status['current_training_id'] - 1
    else:
        status['current_model_id'] = None

    status['current_h5_id'] = find_empty_directory(master_config['h5_path'])
    ##If data exists, start training model
    #if status['current_h5_id'] > 0:
    #     ML_task_queue.add_task(ml_task(ML_config,master_config['h5_path'],master_config['model_path'],status['current_training_id'],remove_existing=False))
    #     status['current_training_id'] = status['current_training_id'] + 1
    status['current_molecule_id']=0
    status['lifetime_failed_builder_tasks'] = 0
    status['lifetime_failed_sampler_tasks'] = 0
    status['lifetime_failed_ML_tasks'] = 0
    status['lifetime_failed_QM_tasks'] = 0

with open(master_config['status_path'], "w") as outfile:
    json.dump(status, outfile, indent=2)

######################################
# Step 4: Check if testing requested #
######################################

testing = False

if '--test_builder' in sys.argv[2:] or '--test_sampler' in sys.argv[2:] or '--test_qm' in sys.argv[2:]:
    builder_task_queue.add_task(builder_task('test_builder',builder_config))
    builder_configuration = builder_task_queue.task_list[0].result()
    queue_output = builder_task_queue.get_task_results()
    test_configuration = queue_output[0][0]
    system_checker(test_configuration)
    print("Builder testing returned:")
    print(test_configuration)
    testing=True
    
if '--test_sampler' in sys.argv[2:]:
    #Check that there is a model available
    next_model = find_empty_directory(master_config['model_path'])
    if status['current_model_id']==None:
        raise RuntimeError("Need to train model before testing sampling")
    print(master_config['model_path'].format(status['current_model_id']))
    sampler_task_queue.add_task(sampler_task(test_configuration,sampler_config,master_config['model_path'].format(status['current_model_id'])))
    sampled_configuration = sampler_task_queue.task_list[0].result()
    queue_output = sampler_task_queue.get_task_results()
    test_configuration = queue_output[0][0]
    system_checker(test_configuration)
    print("Sampler testing returned:")
    print(test_configuration)
    testing=True

#def ase_calculator_task(input_system,configuration_list,directory,command,properties=['energy','forces']):
if '--test_qm' in sys.argv[2:]:
    QM_task_queue.add_task(qm_task(test_configuration,QM_config,master_config['QM_scratch_dir'] + '/' + test_configuration[0]['moleculeid'] + '/',list(master_config['properties_list'])))
    qm_result = QM_task_queue.task_list[0].result()
    queue_output = QM_task_queue.get_task_results()
    test_configuration = queue_output[0][0]
    system_checker(test_configuration)
    print("QM testing Returned:")
    print(test_configuration)
    testing=True
    
#train_ANI_model_task(configuration,data_directory,model_path,model_index,remove_existing=False):
if '--test_ml' in sys.argv[2:]:
    #configuration,data_directory,model_path,model_index,remove_existing=False
    ML_task_queue.add_task(ml_task(ML_config,master_config['h5_dir'],master_config['model_path'],status['current_training_id'],master_config['nGPU'],remove_existing=False))
    status['current_training_id'] = status['current_training_id'] + 1
    with open(master_config['status_path'], "w") as outfile:
        json.dump(status, outfile, indent=2)
    ml_result = ML_task_queue.task_list[0].result()
    queue_output = QM_task_queue.get_task_results()
    returned_models = queue_output[0]
    print("ML training Returned:")
    print(returned_models)
    testing=True
    
if testing:
    exit()
    
########################
# Step 5: Bootstraping #
########################
    
#If there is no data and no models, start boostrap jobs
if status['current_h5_id']==0 and status['current_model_id'] == None:
    print("Building Bootstrap Set")
    while QM_task_queue.get_completed_number() < master_config['bootstrap_set']:
        if (QM_task_queue.get_queued_number() < master_config['target_queued_QM']):
            while (builder_task_queue.get_number() < master_config['parallel_samplers']):
                builder_task_queue.add_task(builder_task('mol-boot-{:10d}'.format(status['current_molecule_id']),builder_config))
                status['current_molecule_id'] = status['current_molecule_id'] + 1
        
        if (builder_task_queue.get_completed_number()>master_config['minimum_QM']):
            builder_results,failed = builder_task_queue.get_task_results()
            status['lifetime_failed_builder_tasks'] = status['lifetime_failed_builder_tasks'] + failed
            for structure in builder_results:
                system_checker(structure)
                QM_task_queue.add_task(qm_task(structure,QM_config,master_config['QM_scratch_dir'] + '/' + structure[0]['moleculeid'] + '/',list(master_config['properties_list'])))
                
        print("### Bootstraping Learning Status at: " + time.ctime() + " ###")
        print("builder status:")
        builder_task_queue.print_status()
        print("QM status:")
        QM_task_queue.print_status()
    
        with open(master_config['status_path'], "w") as outfile:
            json.dump(status, outfile, indent=2)
            
        sleep(60)
    
    print("Saving Bootstrap and training model")    
    store_current_data(master_config['h5_path'].format(status['current_h5_id']),results_list,master_config['properties_list'])
    status['current_h5_id'] = status['current_h5_id'] + 1
    ML_task_queue.add_task(ml_task(ML_config,master_config['h5_dir'],master_config['model_path'],status['current_training_id'],remove_existing=True))
    status['current_training_id'] = status['current_training_id'] + 1
    
    network = ML_task_queue.task_list[0].result()
    if all(network[0]):
        status['current_model_id'] = network[1]
    else:
        print("Bootstrap network failed to train")
        print("User Investigation Required")
        exit()


##################################
## Step 6: Begin Active Learning #
##################################
while True:
    #Re-load configurations, but watch for stupid errors
    try:
        # Load the master configuration:
        master_config_new = load_config_file(sys.argv[1])
        if 'master_directory' not in master_config:
            master_config_new['master_directory'] = None

        # Load the builder config:
        builder_config_new = load_config_file(master_config_new['builder_config_path'],master_config_new['master_directory'])
        
        # Load the sampler config:
        sampler_config_new = load_config_file(master_config_new['sampler_config_path'],master_config_new['master_directory'])
        
        # Load the QM config:
        QM_config_new = load_config_file(master_config_new['QM_config_path'],master_config_new['master_directory'])
        
        # Load the ML config:
        ML_config_new = load_config_file(master_config_new['ML_config_path'],master_config_new['master_directory'])
    except Exception as e:
        print("Failed to re-load configu files:")
        print(e)
    else:
        master_config = master_config_new
        builder_config = builder_config_new
        sampler_config = sampler_config_new
        QM_config = QM_config_new
        ML_config = ML_config_new
	
    #Run more builders
    if (QM_task_queue.get_queued_number() < master_config['target_queued_QM']):
        while (sampler_task_queue.get_number()+builder_task_queue.get_number() < master_config['parallel_samplers']):
            builder_task_queue.add_task(builder_task('mol-{:04d}-{:010d}'.format(status['current_model_id'],status['current_molecule_id']),builder_config))
            status['current_molecule_id'] = status['current_molecule_id'] + 1
            
    #Builders go stright into samplers
    if builder_task_queue.get_completed_number() > 0:
        structure_list,failed = builder_task_queue.get_task_results()
        status['lifetime_failed_builder_tasks'] = status['lifetime_failed_builder_tasks'] + failed
        for structure in structure_list:
            system_checker(structure)
            sampler_task_queue.add_task(sampler_task(structure,sampler_config,master_config['model_path'].format(status['current_model_id'])))

    #Run more QM
    if (sampler_task_queue.get_completed_number()>master_config['minimum_QM']):
        sampler_results,failed = sampler_task_queue.get_task_results()
        status['lifetime_failed_sampler_tasks'] = status['lifetime_failed_sampler_tasks'] + failed
        for structure in sampler_results: #may need [0]
            if not structure[1]==None:
                system_checker(structure)
                QM_task_queue.add_task(qm_task(structure,QM_config,master_config['QM_scratch_dir'] + '/' + structure[0]['moleculeid'] + '/',list(master_config['properties_list'])))

    #Train more models
    if (QM_task_queue.get_completed_number() > master_config['save_h5_threshold']) and (ML_task_queue.get_number() < 1):
        #print(QM_task_queue.task_list[0].result())
    	  #store_current_data(h5path, system_data, properties):
        results_list,failed = QM_task_queue.get_task_results()
        status['lifetime_failed_QM_tasks'] = status['lifetime_failed_QM_tasks'] + failed
        #with open('temp-{:04d}.pkl'.format(status['current_h5_id']),'wb') as pickle_file:
        #    pickle.dump(results_list,pickle_file)
        store_current_data(master_config['h5_path'].format(status['current_h5_id']),results_list,master_config['properties_list'])
        status['current_h5_id'] = status['current_h5_id'] + 1
        ML_task_queue.add_task(ml_task(ML_config,master_config['h5_dir'],master_config['model_path'],status['current_training_id'],master_config['gpus_per_node'],remove_existing=True))
        status['current_training_id'] = status['current_training_id'] + 1
        
    #Update Model
    if ML_task_queue.get_completed_number() > 0:
        output,failed = ML_task_queue.get_task_results()
        status['lifetime_failed_ML_tasks'] = status['lifetime_failed_ML_tasks'] + failed
        for network in output:
            if all(network[0]) and network[1]>status['current_model_id']:
                print('New Model: {:04d}'.format(network[1]))
                status['current_model_id'] = network[1]
                
    print("### Active Learning Status at: " + time.ctime() + " ###")
    print("builder status:")
    builder_task_queue.print_status()
    print("sampling status:")
    sampler_task_queue.print_status()
    print("QM status:")
    QM_task_queue.print_status()
    print("ML status:")
    ML_task_queue.print_status()
    
    with open(master_config['status_path'], "w") as outfile:
        json.dump(status, outfile, indent=2)
    
    time.sleep(60)
	
    
