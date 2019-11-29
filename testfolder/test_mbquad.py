from mbrl.network import Dynamics
from mbrl.wrapped_env import QuadrotorEnv
from mbrl.mpc import RandomShooter

from utils.rolls import rollouts
from utils.gen_trajectories import Trajectory
from utils.utility import DecodeEnvironment
import os
import json
import glob

import torch

from IPython.core.debugger import set_trace

id_execution_test   =   '8'
#set_trace()
restore_folder  ='./data/sample40/'
save_paths_dir  =   os.path.join(restore_folder, 'rolls'+id_execution_test)
#save_paths_dir  =   None
with open(os.path.join(restore_folder,'config_train.json'), 'r') as fp:
    config_train    =   json.load(fp)
    

"""
@Params:
trajectory_type:    Select one of the following flags

    'stepped',      : Generate an stepped trajectory: first 48 % max_path_length of timesteps at point (0.8,0.8,0.8) then (0.0,0.0,0.0)
    'sin_vertical'  : Generate a Sinusoidal trajectory in plane y=0.0, z = sin(x)        
    'circle'        : Generate a Circular Trajectory in plane z = 0.5, x = sin(t), y=sin(t)
    'helicoid'       : Generate a vertical Helicoid: z = t, x = cos(t), y=sin(t)

horizon:            The horizon used by the MPC, ussually between 15-20
candidates:         The number of candidates used by the MPC, ussually between 1000-2000
nstack:             Number of stacked past state-actions, must be the same as in training step

"""

config      =   {
    "env_name"          :   config_train['env_name'],
    "horizon"           :   20,
    "candidates"        :   1500,
    "discount"          :   0.99,
    "nstack"            :   config_train['nstack'],
    #"reward_type"       :   config_train['reward_type'],
    "reward_type"       :   config_train['reward_type'],
    "max_path_length"   :   1250,
    "nrollouts"         :   20,
    "trajectory_type"   :   'point',
    "sthocastic"        :   False,
    "hidden_layers"     :   config_train['hidden_layers'],
    "crippled_rotor"    :   config_train['crippled_rotor']
}

env_class       =   DecodeEnvironment(config['env_name'])
env_            =   env_class(port=28001, reward_type=config['reward_type'], fault_rotor=config['crippled_rotor'])
state_shape     =   env_.observation_space.shape
action_shape    =   env_.action_space.shape

config['dt']    =   env_.dt
device          =   torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')


dynamics        =   Dynamics(state_shape, action_shape, stack_n=config['nstack'], sthocastic=config['sthocastic'], hlayers=config['hidden_layers'])
rs              =   RandomShooter(config['horizon'], config['candidates'], env_, dynamics, device, config['discount'])
checkpoint      =   torch.load(os.path.join(restore_folder, 'params_high.pkl'))
dynamics.load_state_dict(checkpoint['model_state_dict'])

dynamics.mean_input =   checkpoint['mean_input']
dynamics.std_input  =   checkpoint['std_input']
dynamics.epsilon    =   checkpoint['epsilon']

dynamics.to(device)

""" Send a Trajectory to follow"""
trajectoryMaganger  =   Trajectory(config['max_path_length'], 2)
trajectory          =   trajectoryMaganger.gen_points(config['trajectory_type']) if config['trajectory_type'] is not None else None

proportion          =   round(config_train['time_step_size']/config['dt'])
if save_paths_dir is not None:
    configsfiles    =   glob.glob(os.path.join(save_paths_dir,'*.json'))
    files_paths     =   glob.glob(os.path.join(save_paths_dir,'*.pkl'))

    assert len(configsfiles) ==0, 'Already the folder is busy, select other'
    assert len(files_paths)==0, 'Already the folder is busy, select another one'
    if not os.path.exists(save_paths_dir):
        os.makedirs(save_paths_dir)
    
    with open(os.path.join(save_paths_dir, 'experiment_config.json'), 'w') as fp:
        json.dump(config, fp, indent=2)

rollouts(dynamics, env_, rs, config['nrollouts'], config['max_path_length'], save_paths_dir, trajectory, proportion)

env_.close()
