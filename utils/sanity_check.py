from mbrl.network import Dynamics
from mbrl.mpc import RandomShooter
#from rolls import rollouts
from mbrl.runner import StackStAct
from mbrl.wrapped_env import QuadrotorEnv, QuadrotorAcelRotmat
from utils.gen_trajectories import Trajectory

from utils.analize_dynamics import plot_error_map, plot_multiple_error_map
from utils.utility import DecodeEnvironment
import numpy as np
import random
import torch
from itertools import count

import os
import json

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

    def __init__(self, h, c, dynamics:Dynamics, mpc, env, t_init, traj, n_steps=1, max_path_length=250):
        """ Variables of policy"""
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
        self.n_steps            =   n_steps


    def rollouts(self, flat_functions, env_classes, configfiles, min_path_length=100):
        trajectory      =   self.trajectory
        nstack          =   self.nstack
        mpc             =   self.mpc
        dynamics        =   self.dynamics
        max_path_length =   self.max_path_length
        timestep        =   0
        nstacks         =   [configfile['nstack'] for configfile in configfiles]

        while timestep < min_path_length:
            if trajectory is None:
               targetposition  =   0.8 * np.ones(3, dtype=np.float32)
            else:
               targetposition  =   trajectory[0]

            next_target_pos =   targetposition

            self.env.set_targetpos(targetposition)
            obs = self.env.reset(np.array([0., 0., 0.]), np.array([0., 0., 0.]))
            obses           =   self.env.last_observation
            obses           =   [flat_fn(obses) for flat_fn in flat_functions]

            stack_as_policy =   StackStAct(self.env.action_space.shape, self.env.observation_space.shape, n=nstack, init_st=obs)
            stack_as_list   =   [StackStAct(_envclass._get_action_space().shape, _envclass._get_state_space().shape, n=_nstack, init_st=_ob) for _envclass, _nstack, _ob in zip(env_classes, nstacks, obses)]

            done            =   False
            timestep        =   0
            cum_reward      =   0.0

            running_paths=[dict(observations=[], actions=[], rewards=[], dones=[], next_obs=[], target=[]) for _ in range(len(flat_functions))]

            while not done and timestep < max_path_length:

                if timestep == 120 and trajectory is None:
                    next_target_pos  = np.zeros(3, dtype=np.float32)
                elif trajectory is not None:
                    next_target_pos =   trajectory[timestep + 1]

                self.env.set_targetpos(next_target_pos)

                #action = mpc.get_action_PDDM(stack_as, 0.6, 5)
                action = mpc.get_action_torch(stack_as_policy)

                next_obs, reward, done, _   =  self.env.step(action)

                [_stack_as.append(acts=action) for _stack_as in stack_as_list]
                stack_as_policy.append(acts=action)

                #if save_paths is not None:
                for _idx, _stack_as in zip(range(len(flat_functions)), stack_as_list):
                    observation, action = _stack_as.get()
                    running_paths[_idx]['observations'].append(observation.flatten())
                    running_paths[_idx]['actions'].append(action.flatten())
                    running_paths[_idx]['rewards'].append(reward)
                    running_paths[_idx]['dones'].append(done)
                    running_paths[_idx]['next_obs'].append(next_obs)
                    running_paths[_idx]['target'].append(targetposition)

                #if done or len(running_paths['rewards']) >= max_path_length:
                #    #print('ohhhh')
                #    paths.append(dict(
                #        observation=np.asarray(running_paths['observations']),
                #        actions=np.asarray(running_paths['actions']),
                #        rewards=np.asarray(running_paths['rewards']),
                #        dones=np.asarray(running_paths['dones']),
                #        next_obs=np.asarray(running_paths['next_obs']),
                #        target=np.asarray(running_paths['target'])
                #    ))


                targetposition  =   next_target_pos
                obses           =   self.env.last_observation

                [_stack_as.append(obs=_flat_fn(obses)) for _stack_as, _flat_fn in zip(stack_as_list, flat_functions)]

                stack_as_policy.append(obs=next_obs)
                cum_reward  +=  reward
                timestep += 1
        
        return running_paths
    
    def _get_configfiles(self, samples_names:list):
        config_filenames    =   [os.path.join(sample_name, 'config_train.json') for sample_name in samples_names]
        exists_files        =   [os.path.exists(config_filename) for config_filename in config_filenames]
        
        configfiles         =   []
        #set_trace()
        for filename, exists in zip(config_filenames, exists_files):
            if exists:
                with open(filename) as fp:
                    configfiles.append(json.load(fp))

        return configfiles
    
    def _get_environments(self, configfiles):
        if type(configfiles[0]) == dict:
            envs   =   [DecodeEnvironment(configfile['env_name']) for configfile in configfiles]
        
        elif type(configfiles[0]) == str:
            envs   =   [DecodeEnvironment(envname) for envname in configfiles]
        
        return envs

    def _get_obsflat_functions(self, envs):
        return [env._flat_observation_st for env in envs]

    """ 
        Load Dynamics from list of sample filenames
    """
    def _load_dynamics(self, samples_names, device):
        configfiles     =   self._get_configfiles(samples_names)
        envs            =   self._get_environments(configfiles)
        action_spaces   =   [env._get_action_space() for env in envs]
        observ_spaces   =   [env._get_state_space() for env in envs]
        nstacks         =   [configfile['nstack'] for configfile in configfiles]
        sthocastics     =   [configfile['sthocastic'] for configfile in configfiles]
        hidden_layers   =   [configfile['hidden_layers'] for configfile in configfiles]
        dynamics_list   =   [Dynamics(state_space.shape, action_space.shape, nstack, sthocastic, hlayers=hlayers) for state_space, action_space, nstack, sthocastic, hlayers in zip(observ_spaces, action_spaces, nstacks, sthocastics, hidden_layers)]
        #set_trace()
        """ Loading Dynamics """
        checkpoints     =   [torch.load(os.path.join(sample_name, 'params_high.pkl')) for sample_name in samples_names]
        
        for dynamics, checkpoint in zip(dynamics_list, checkpoints):
            dynamics.load_state_dict(checkpoint['model_state_dict'])
            dynamics.mean_input =   checkpoint['mean_input']
            dynamics.std_input  =   checkpoint['std_input']
            dynamics.epsilon    =   checkpoint['epsilon']
            dynamics.to(device)

        return dynamics_list

    
    def get_ground_thruth_states(self, samples_names):
        
        configfiles         =   self._get_configfiles(samples_names)
        envsclasses         =   self._get_environments(configfiles)
        obsflat_functions   =   self._get_obsflat_functions(envsclasses)

        #set_trace()
        path                =   self.rollouts(obsflat_functions, envsclasses, configfiles)

        return path

    def normalize_input(self, dynamics, obs):
        return SanityCheck.normalize_input_st(dynamics, obs)

    @staticmethod
    def normalize_input_st(dynamics, obs):
        assert dynamics.mean_input is not None
        return (obs - dynamics.mean_input)/(dynamics.std_input + dynamics.epsilon)
    """ 
        Compute quadratic error 
        ::Assume numpy inputs
    """
    def compute_quadratic_error(self, state1, state2):
        return SanityCheck.compute_quadratic_error_st(state1, state2)
        
    @staticmethod
    def compute_quadratic_error_st(state1, state2):
        difference  =   state1- state2
        return np.sqrt(np.sum(difference * difference, axis=0))

    def process_states(self, samples_names):
        #set_trace()
        paths = self.get_ground_thruth_states(samples_names)

        configfiles =   self._get_configfiles(samples_names)
        envsclasses    =   self._get_environments(configfiles)
        action_spaces   =   [envclass._get_action_space() for envclass in envsclasses]
        state_spaces    =   [envclass._get_state_space() for envclass in envsclasses]
        path_length         =   len(paths[0]['observations'])
        set_trace()
        assert path_length > self.t_init + self.horizon + self.n_steps - 1, 'Too short path, try again!'
        
        device              =   torch.device('cuda:0' if torch.cuda.is_available() else 'cpu')
        dynamics_list       =   self._load_dynamics(samples_names, device)
           
        indexes             =   random.sample(range(self.t_init, path_length - self.horizon), self.n_steps)
        indexes.sort()

        """ Collect samples over states collected"""
        states              =   [[path['observations'][idx:idx+self.horizon] for idx in indexes] for path in paths]
        actions             =   [[path['actions'][idx:idx+self.horizon] for idx in indexes] for path in paths]

        """ Concat corresponding state & actions """
        #observations_paths  =   [[np.concatenate((state_unit, action_unit)) for state_unit, action_unit in zip(state_path, action_path)] for state_path, action_path in zip(states, actions)]

        #normalized_observations =   [[self.normalize_input(dynamics, obs) for obs in obs_path] for obs_path, dynamics in zip(observations_paths, dynamics_list)]

        error_matrixes          =   np.zeros((self.horizon, self.n_steps, len(samples_names)), dtype=np.float32)
        for _idx, _states_path, _actions_path, dynamics, action_space, state_space in zip(count(), states, actions, dynamics_list, action_spaces, state_spaces):
            nstack  =   dynamics.stack_n
            for _step, _states_gt, _actions_gt in zip(count(), _states_path, _actions_path):
                init_stackobs    =  _states_gt[0].reshape(nstack, -1)
                init_stackacts   =  _actions_gt[0].reshape(nstack, -1)
                stack_as = StackStAct(action_space.shape, state_space.shape, n=nstack)
                stack_as.fill_with_stack(init_stackobs, init_stackacts)
                for _h in range(1, self.horizon):
                    obs_, acts_             =   stack_as.get()
                    obs_flat                =   np.concatenate((obs_.flatten(), acts_.flatten()), axis=0)
                    obs_flat                =   self.normalize_input(dynamics, obs_flat)
                    obs_tensor              =   torch.tensor(obs_flat, dtype=torch.float32, device=device)
                    obs_tensor.unsqueeze_(0)
                    next_obs                =   dynamics.predict_next_obs(obs_tensor, device).to('cpu')
                    next_obs                =   np.asarray(next_obs.squeeze(0))
                    next_action             =   _actions_gt[_h][-action_space.shape[0]:]
                    stack_as.append(next_obs, next_action)

                    gt_obs                  =   _states_gt[_h][-state_space.shape[0]:]
                    error_                  =   self.compute_quadratic_error(gt_obs, next_obs)
                    error_matrixes[_h, _step, _idx] =   error_

                    #art_states.append(next_obs)
                    #normalize_observation   =   self.normalize_input(dynamics, observations)
                    #normalize_observation   =   torch.tensor(normalize_observation, dtype=torch.float32, device=device)

        print('Deal with normalized observation')
        #return normalized_observations
        return error_matrixes
    
    @staticmethod
    def get_errors_matrixes_from_path(path, action_sz, state_sz, horizon, nstack, dynamics, device, nskip=1):
        """ Get matrix of error from run path"""
        length_path         =   path['actions'].shape[0]
        states              =   [path['observation'][idx:idx+horizon] for idx in range(length_path-horizon)]
        actions             =   [path['actions'][idx:idx+horizon] for idx in range(length_path-horizon)]

        set_trace()
        """ Concat corresponding state & actions """
        #observations_paths  =   [[np.concatenate((state_unit, action_unit)) for state_unit, action_unit in zip(state_path, action_path)] for state_path, action_path in zip(states, actions)]

        #normalized_observations =   [[self.normalize_input(dynamics, obs) for obs in obs_path] for obs_path, dynamics in zip(observations_paths, dynamics_list)]

        error_matrixes          =   np.zeros((horizon, length_path), dtype=np.float32)
        
        nstack  =   dynamics.stack_n
        for _step, _sts, _acts in zip(count(), states, actions):
            init_stackobs    =  _sts[0].reshape(nstack, -1)
            init_stackacts   =  _acts[0].reshape(nstack, -1)
            stack_as = StackStAct((action_sz,), (state_sz,), n=nstack)
            stack_as.fill_with_stack(init_stackobs, init_stackacts)
            for _h in range(1, horizon):
                obs_, acts_             =   stack_as.get()
                obs_flat                =   np.concatenate((obs_.flatten(), acts_.flatten()), axis=0)
                obs_flat                =   SanityCheck.normalize_input_st(dynamics, obs_flat)
                obs_tensor              =   torch.tensor(obs_flat, dtype=torch.float32, device=device)
                obs_tensor.unsqueeze_(0)
                next_obs                =   dynamics.predict_next_obs(obs_tensor, device).to('cpu')
                next_obs                =   np.asarray(next_obs.squeeze(0))
                next_action             =   _acts[_h][-action_sz:]
                stack_as.append(next_obs, next_action)

                gt_obs                  =   _sts[_h][-state_sz:]
                error_                  =   SanityCheck.compute_quadratic_error_st(gt_obs, next_obs)
                error_matrixes[_h, _step] =   error_

        return error_matrixes
    

    def get_state_actions(self):
        """ Generate one rollout """
        set_trace()
        path    =   rollouts(self.dynamics, self.env, self.mpc, 1, self.max_path_length, None, self.trajectory)
        #gt_states   =   path[0]['observation'][self.t_init:, 18*(self.nstack-1):]
        assert len(path[0]['observations']) > self.t_init + self.horizon + self.n_steps - 1, 'Too short path, try again!'
        gt_states   =   path[0]['observation'][self.t_init:self.t_init + self.horizon + self.n_steps - 1,:]
        gt_actions  =   path[0]['actions'][self.t_init:self.t_init + self.horizon + self.n_steps - 1,:]

        L = []

        for step in range(self.n_steps):
            init_stackobs    =  gt_states[step].reshape(self.nstack, -1)
            init_stackacts   =  gt_actions[step].reshape(self.nstack, -1)
            
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

            L.append(((gt_states[step:step + self.horizon, self.obs_flat_size * (self.nstack - 1):], gt_actions[step:step+self.horizon,self.env.action_space.shape[0] * (self.nstack - 1):]), (np.stack(art_states, axis=0), np.stack(art_actions,axis=0))))
        return L

    def analize_errors(self, gt_states, ar_states):
        import matplotlib.pyplot as plt
        errors  =   np.sqrt(np.sum((gt_states-ar_states)*(gt_states-ar_states), axis=1))
        t       =   np.arange(len(errors))
        plt.plot(t, errors)
        plt.show()
    
    def get_errors(self, gt_states, ar_states):
        errors  =   np.sqrt(np.sum((gt_states-ar_states)*(gt_states-ar_states), axis=1))
        return errors

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

    restore_folder  ='./data/sample40/'
    #save_paths_dir  =   os.path.join(restore_folder, 'rolls'+id_execution_test)
    #save_paths_dir  =   None
    with open(os.path.join(restore_folder,'config_train.json'), 'r') as fp:
        config_train    =   json.load(fp)

    config      =   {
        "env_name"          :   config_train['env_name'],
        "horizon"           :   20,
        "candidates"        :   1500,
        "discount"          :   0.99,
        "t_init"            :   15,
        "nstack"            :   config_train['nstack'],
        #"reward_type"       :   config_train['reward_type'],
        "reward_type"       :   'type5',
        "max_path_length"   :   250,
        "nrollouts"         :   20,
        "n_steps"           :   40,
        "trajectory_type"   :   'point',
        "sthocastic"        :   False,
        "hidden_layers"     :   config_train['hidden_layers'],
        "crippled_rotor"    :   config_train['crippled_rotor']
    }
    env_class       =   DecodeEnvironment(config['env_name'])
    env_            =   env_class(port=28001, reward_type=config['reward_type'], fault_rotor=config['crippled_rotor'])
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

    scheck              =   SanityCheck(config['horizon'],config['candidates'],dynamics,rs,env_, config['t_init'], trajectory, config['n_steps'], config['max_path_length'])

    folders =   ['./data/sample40','./data/sample32']#, './data/sample27', './data/sample29', './data/sample30','./data/sample31']

    mat_error   =   scheck.process_states(folders)

    plot_multiple_error_map(mat_error, config['n_steps'], _vmax=10.0)
    #(gt_s, gt_a), (ar_s, ar_a)  =   scheck.get_state_actions()
    #L   =   scheck.get_state_actions()
    ##scheck.analize_errors(gt_s,ar_s)
    ##scheck.analize_pos_error(gt_s,ar_s)
    #
    #error_list  =   []
    #for (gt_s, gt_a), (ar_s, ar_a) in L:
    #    errors  =   scheck.get_errors(gt_s, ar_s)
    #    error_list.append(errors)
    #errors  =   np.vstack(error_list)
#
    ##errors  =   np.repeat(errors, 15).reshape(15,15)
    #plot_error_map(errors.T, _vmax=10.0)
#
    #(gt_s, gt_a), (ar_s, ar_a) = L[0]
#
    #scheck.analize_errors(gt_s, ar_s)
#
    #(gt_s, gt_a), (ar_s, ar_a) = L[4]
#
    #scheck.analize_errors(gt_s, ar_s)
    

    env_.close()

    print(10)