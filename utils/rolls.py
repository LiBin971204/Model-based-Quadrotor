from mbrl.mpc import RandomShooter
from mbrl.network import Dynamics
from mbrl.wrapped_env import QuadrotorEnv
from mbrl.runner import StackStAct
from collections import deque

import numpy as np
import joblib
import os
import glob

def rollouts(dynamics:Dynamics, env:QuadrotorEnv, mpc:RandomShooter, n_rolls=20, max_path_length=250, save_paths=None, traj=None, proportion=1, initial_states=dict(pos=None,ang=None), runn_all_steps=False):
    """ Generate rollouts for testing & Save paths if it is necessary"""
    nstack  =   dynamics.stack_n
    paths   =   []

    if save_paths is not None:
        pkls    =   glob.glob(os.path.join(save_paths, '*.pkl'))
        assert len(pkls) == 0, "Selected directory is busy, please select other"
        log_path    =   os.path.join(save_paths, 'log.txt')
        texto   =   'Prepare for save paths in "{}"\n'.format(save_paths)

        print('Prepare for save paths in "{}"'.format(save_paths))
    
    print('Initial states is Fixed!' if initial_states['pos']is not None else 'Initial states is selected Randomly')
    print('Allowed to execute all t-steps' if runn_all_steps else 'Early stop activated')
    #env.set_targetpos(np.random.uniform(-1.0, 1.0, size=(3,)))
    for i_roll in range(1, n_rolls+1):
        #targetposition  =   np.random.uniform(-1.0, 1.0, size=(3))
        if traj is None:
            targetposition  =   0.8 * np.ones(3, dtype=np.float32)
        else:
            targetposition  =   traj[0]
        
        next_target_pos =   targetposition

        env.set_targetpos(targetposition)
        init_pos    =   initial_states['pos']
        init_ang    =   initial_states['ang']
        obs = env.reset(init_pos, init_ang)
        

        stack_as = StackStAct(env.action_space.shape, env.observation_space.shape, n=nstack, init_st=obs)
        done = False
        timestep    =   0
        cum_reward  =   0.0

        # Test stacked
        indexes                 =   [idx * proportion for idx in range(nstack)]
        window_length           =   (nstack - 1)*proportion + 1
        stack_states_proportion =   deque(maxlen=window_length)
        stack_actions_proportion    =   deque(maxlen=window_length)


        running_paths=dict(observations=[], actions=[], rewards=[], dones=[], next_obs=[], target=[])

        while not done and timestep < max_path_length:
            
            if timestep == 120 and traj is None:
                next_target_pos  = np.zeros(3, dtype=np.float32)
            elif traj is not None:
                next_target_pos =   traj[timestep + 1]

            env.set_targetpos(next_target_pos)

            if len(stack_states_proportion) >= window_length:
                actions_    =   [stack_actions_proportion[idx] for idx in indexes]
                states_    =   [stack_states_proportion[idx] for idx in indexes]
                stack_as.fill_with_stack(states_, actions_)
            #action = mpc.get_action_PDDM(stack_as, 0.6, 5)
            action = mpc.get_action_torch(stack_as)
               
            next_obs, reward, done, env_info =   env.step(action)

            if runn_all_steps: done=False

            if len(stack_states_proportion) < window_length:
                stack_as.append(acts=action)
            
            # Test stacked
            stack_actions_proportion.append(action)

            #if save_paths is not None:
            observation, action = stack_as.get()
            running_paths['observations'].append(observation.flatten())
            running_paths['actions'].append(action.flatten())
            running_paths['rewards'].append(reward)
            running_paths['dones'].append(done)
            running_paths['next_obs'].append(next_obs)
            running_paths['target'].append(targetposition)

            if done or len(running_paths['rewards']) >= max_path_length:
                #print('ohhhh')
                paths.append(dict(
                    observation=np.asarray(running_paths['observations']),
                    actions=np.asarray(running_paths['actions']),
                    rewards=np.asarray(running_paths['rewards']),
                    dones=np.asarray(running_paths['dones']),
                    next_obs=np.asarray(running_paths['next_obs']),
                    target=np.asarray(running_paths['target'])
                ))
            # endif
            
            targetposition  =   next_target_pos
            if len(stack_states_proportion) < window_length:
                stack_as.append(obs=next_obs)
            # Test stacked
            stack_states_proportion.append(next_obs)

            cum_reward  +=  reward
            timestep += 1

        newtexto   = '{} rollout, reward-> {} in {} timesteps'.format(i_roll, cum_reward, timestep)
        if save_paths is not None:
            joblib.dump(paths, os.path.join(save_paths, 'paths.pkl'))
            with open(log_path, 'w') as fp:
                texto   +=  newtexto + '\n'
                fp.write(texto)


        print(newtexto)

    return paths

