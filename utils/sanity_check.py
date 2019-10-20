from mbrl.network import Dynamics
from mbrl.mpc import RandomShooter
from rolls import rollouts
from mbrl.runner import StackStAct
from mbrl.wrapped_env import QuadrotorEnv
from utils.gen_trajectories import Trajectory
import numpy as np
import torch

from IPython.core.debugger import set_trace

class SanityCheck:
    """
        Sanity Check class
        ===================
        Provides an interface to check how well the dynamics is predicting
        Given an initial state S0, taken at point "t_init" time-step, predict the
        following "horizon" future timesteps.

        A comparison is provided between the states generated by the artificial dynamics
        and the ground truth dynamics. 

        First, actions are generated by the MPC and dynamics, the resulting true-actions
        and true-states are stored.
        Then, the same actions is taken from the initial state to generate a new set of states
        these new states are stored as artificial-states, the a comparison is performed.

        @Parameters:
        h               :   horizon
        c               :   number of candidates to the MPC
        mpc             :   Model Predictive Controler
        env             :   Environment, 'QuadrotorEnv'
        t_init          :   t_step to take the initial State
        traj            :   Trajectory over will be generated the states
        max_path_length :   The maximum length of a path
    """

    def __init__(self, h, c, dynamics:Dynamics, mpc, env, t_init, traj, max_path_length=250):
        self.horizon            =   h
        self.candidates         =   c
        self.dynamics           =   dynamics
        self.mpc                =   mpc
        self.env                =   env
        self.t_init             =   t_init
        self.trajectory         =   traj
        self.max_path_length    =   max_path_length
        self.nstack             =   dynamics.stack_n
        self.obs_flat_size      =   self.env.observation_space.shape[0]



    def get_state_actions(self):
        """ Generate one rollout """
        #set_trace()
        path    =   rollouts(self.dynamics, self.env, self.mpc, 1, self.max_path_length, None, self.trajectory)
        #gt_states   =   path[0]['observation'][self.t_init:, 18*(self.nstack-1):]
        gt_states   =   path[0]['observation'][self.t_init:self.t_init + self.horizon,:]
        gt_actions  =   path[0]['actions'][self.t_init:self.t_init + self.horizon,:]

        init_stackobs    =  gt_states[0].reshape(self.nstack, -1)
        init_stackacts   =  gt_actions[0].reshape(self.nstack, -1)
           
        stack_as = StackStAct(self.env.action_space.shape, self.env.observation_space.shape, n=self.nstack)

        stack_as.fill_with_stack(init_stackobs, init_stackacts)
        
        device      =   next(self.dynamics.parameters()).device
        art_states  =   [stack_as.get_last_state()]
        art_actions =   [stack_as.get_last_action()]

        for i in range(1, self.horizon):
            obs_, acts_ =   stack_as.get()
            obs_flat    =   np.concatenate((obs_.flatten(), acts_.flatten()), axis=0)   
            obs_flat    =   self.mpc.normalize_(obs_flat)
            obs_tensor  =   torch.tensor(obs_flat, dtype=torch.float32, device=device)
            obs_tensor.unsqueeze_(0)
            next_obs    =   self.dynamics.predict_next_obs(obs_tensor, device).to('cpu')
            next_obs    =   np.asarray(next_obs.squeeze(0))
            next_action =   gt_actions[i,self.env.action_space.shape[0] * (self.nstack - 1):]
            stack_as.append(next_obs, next_action)

            art_states.append(next_obs)
            art_actions.append(next_action)

        return (gt_states[:, self.obs_flat_size * (self.nstack - 1):], gt_actions[:,self.env.action_space.shape[0] * (self.nstack - 1):]), (np.stack(art_states, axis=0), np.stack(art_actions,axis=0))

    def analize_errors(self, gt_states, ar_states):
        import matplotlib.pyplot as plt
        errors  =   np.sqrt(np.sum((gt_states-ar_states)*(gt_states-ar_states), axis=1))
        t       =   np.arange(len(errors))
        plt.plot(t, errors)
        plt.show()
    def analize_pos_error(self, gt_states, ar_states):
        import matplotlib.pyplot as plt
        gt_pos  =   gt_states[:, 9:12]
        ar_pos  =   ar_states[:, 9:12]
        errors  =   np.sqrt(np.sum((gt_pos-ar_pos)*(gt_pos-ar_pos), axis=1))
        t       =   np.arange(len(errors))
        plt.plot(t, errors)
        plt.show()

if __name__ == "__main__":
    import os
    import json

    restore_folder  ='./data/sample16/'
    #save_paths_dir  =   os.path.join(restore_folder, 'rolls'+id_execution_test)
    #save_paths_dir  =   None
    with open(os.path.join(restore_folder,'config_train.json'), 'r') as fp:
        config_train    =   json.load(fp)

    config      =   {
        "horizon"           :   20,
        "candidates"        :   1500,
        "discount"          :   0.99,
        "t_init"            :   30,
        "nstack"            :   config_train['nstack'],
        #"reward_type"       :   config_train['reward_type'],
        "reward_type"       :   'type1',
        "max_path_length"   :   250,
        "nrollouts"         :   20,
        "trajectory_type"   :   'sin-vertical',
        "sthocastic"        :   False,
        "hidden_layers"     :   config_train['hidden_layers'],
        "crippled_rotor"    :   config_train['crippled_rotor']
    }

    env_            =   QuadrotorEnv(port=28001, reward_type=config['reward_type'], fault_rotor=config['crippled_rotor'])
    state_shape     =   env_.observation_space.shape
    action_shape    =   env_.action_space.shape

    device      =   torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
    """ Load dynamics """
    dynamics    =   Dynamics(state_shape, action_shape, stack_n=config['nstack'], sthocastic=config['sthocastic'], hlayers=config['hidden_layers'])
    rs          =   RandomShooter(config['horizon'], config['candidates'], env_, dynamics, device, config['discount'])
    checkpoint  =   torch.load(os.path.join(restore_folder, 'params_high.pkl'))
    dynamics.load_state_dict(checkpoint['model_state_dict'])

    dynamics.mean_input =   checkpoint['mean_input']
    dynamics.std_input  =   checkpoint['std_input']
    dynamics.epsilon    =   checkpoint['epsilon']

    dynamics.to(device)
    #set_trace()
    """ Send a Trajectory to follow"""
    trajectoryManager   =   Trajectory(config['max_path_length'], 2)
    trajectory          =   trajectoryManager.gen_points(config['trajectory_type']) if config['trajectory_type'] is not None else None

    scheck              =   SanityCheck(config['horizon'],config['candidates'],dynamics,rs,env_, config['t_init'], trajectory, config['max_path_length'])

    (gt_s, gt_a), (ar_s, ar_a)  =   scheck.get_state_actions()
    scheck.analize_errors(gt_s,ar_s)
    scheck.analize_pos_error(gt_s,ar_s)
    env_.close()

    print(10)