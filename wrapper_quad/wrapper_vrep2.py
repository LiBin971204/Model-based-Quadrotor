import gym
from gym import spaces
import numpy as np
import wrapper_quad.vrep as vrep
from typing import NoReturn
import time
from random import gauss


class VREPQuadAccel(gym.Env):
    def __init__(self, ip='127.0.0.1', port=19997, envname='Quadricopter', targetpos=np.zeros(3, dtype=np.float32)):
        super(VREPQuadAccel, self).__init__()
        # Initialize vrep
        self.envname            =   envname
        clientID                =   vrep.simxStart(ip, port, True, True, 5000, 0)
    
        if clientID != -1:
            print('Connection Established Successfully to IP> {} - Port> {} - ID: {}'.format(ip, port, clientID))
            self.clientID       =   clientID
            self.targetpos      =   targetpos
            _, self.dt          =   vrep.simxGetFloatingParameter(self.clientID, vrep.sim_floatparam_simulation_time_step, vrep.simx_opmode_oneshot_wait)

            self.prev_linvel    =   np.zeros(3, dtype=np.float32)
            self.prev_angvel    =   np.zeros(3, dtype=np.float32)
            #self.prev_pos
            print('Initialized with tstep>\t{}'.format(vrep.simxGetFloatingParameter(self.clientID, vrep.sim_floatparam_simulation_time_step, vrep.simx_opmode_oneshot_wait)))
        else:
            raise ConnectionError("Can't Connect with the envinronment at IP:{}, Port:{}".format(ip, port))
        
        ## Detach object target_get_random_pos_ang
        r, self.target_handler      =   vrep.simxGetObjectHandle(clientID, 'Quadricopter_target', vrep.simx_opmode_oneshot_wait)
        vrep.simxSetObjectParent(clientID, self.target_handler, -1, True, vrep.simx_opmode_oneshot_wait)
        # Set signal debug:
        vrep.simxSetIntegerSignal(self.clientID, 'signal_debug', 1337, vrep.simx_opmode_oneshot)
        r, self.quad_handler         =   vrep.simxGetObjectHandle(clientID, self.envname, vrep.simx_opmode_oneshot_wait)

        print(r, self.quad_handler)
        # Define gym variables

        self.action_space       =   spaces.Box(low=0.0, high=100.0, shape=(4,), dtype=np.float32)

        self.observation_space  =   spaces.Box(low=-np.inf, high=np.inf, shape=(18,), dtype=np.float32)

        # Get scripts propellers Here...!
        #self.propsignal =   ['joint' + str(i+1) for i in range(0, 4)]
        self.propsignal =   ['speedprop' + str(i+1) for i in range(0, 4)]

    def step(self, action:np.ndarray):
        for act, name in zip(action, self.propsignal):
            vrep.simxSetFloatSignal(self.clientID, name, act, vrep.simx_opmode_streaming)

        vrep.simxSynchronousTrigger(self.clientID)
        vrep.simxGetPingTime(self.clientID)
        rotmat, position, angvel, linvel =   self._get_observation_state()
        rowdata         =   self._appendtuples_((rotmat, position, angvel, linvel))

        reward          =   position
        distance        =   np.sqrt((reward * reward).sum())
        reward          =   4.0 -1.25 * distance
        done             =   (distance > 3.2)

        return (rowdata, reward, done, dict())
    
    def reset(self):
        vrep.simxStopSimulation(self.clientID, vrep.simx_opmode_blocking)
        try:
            while True:
                vrep.simxGetIntegerSignal(self.clientID, 'signal_debug', vrep.simx_opmode_blocking)
                e   =   vrep.simxGetInMessageInfo(self.clientID, vrep.simx_headeroffset_server_state)
                still_running = e[1] & 1
                if not still_running:
                    break
        except: pass
        r, self.quad_handler        =   vrep.simxGetObjectHandle(self.clientID, self.envname, vrep.simx_opmode_oneshot_wait)
        r, self.target_handler      =   vrep.simxGetObjectHandle(self.clientID, 'Quadricopter_target', vrep.simx_opmode_oneshot_wait)
        # start pose
        init_position, init_ang     =   self._get_random_pos_ang(max_radius=3.1, max_angle=np.pi, respecto=self.targetpos)
        vrep.simxSetObjectPosition(self.clientID, self.quad_handler, -1, init_position, vrep.simx_opmode_blocking)
        vrep.simxSetObjectOrientation(self.clientID, self.quad_handler, -1, init_ang, vrep.simx_opmode_blocking)
        ## Set target
        vrep.simxSetObjectPosition(self.clientID, self.target_handler, -1, self.targetpos, vrep.simx_opmode_oneshot)


        self.startsimulation()
        vrep.simxSynchronousTrigger(self.clientID)
        vrep.simxGetPingTime(self.clientID)
        rdata = self._get_observation_state(False)
        self.prev_pos = np.asarray(rdata[1])
        return self._appendtuples_(rdata)

    def render(self, close=False):
        print('Trying to render')
        # Put code if it is necessary to render
        pass

    def close(self):
        print('Exit connection from ID client> {}'.format(self.clientID))
        vrep.simxClearIntegerSignal(self.clientID, 'signal_debug', vrep.simx_opmode_blocking)
        vrep.simxStopSimulation(self.clientID, vrep.simx_opmode_blocking)
        time.sleep(2.5)
        #writer.close()
        vrep.simxFinish(-1)

    def startsimulation(self):
        if self.clientID != -1:
            vrep.simxSynchronous(self.clientID, True)
            e = vrep.simxStartSimulation(self.clientID, vrep.simx_opmode_blocking)

            #self._set_boolparam(vrep.sim_boolparam_threaded_rendering_enabled, True)
            #print(e)
        else:
            raise ConnectionError('Any conection has been done')

    def _get_observation_state(self, compute_acel = True):
        _, position         =   vrep.simxGetObjectPosition(self.clientID,    self.quad_handler, -1, vrep.simx_opmode_oneshot_wait)
        _, orientation      =   vrep.simxGetObjectOrientation(self.clientID, self.quad_handler, -1, vrep.simx_opmode_oneshot_wait)
        _, lin_vel, ang_vel =   vrep.simxGetObjectVelocity(self.clientID,    self.quad_handler, vrep.simx_opmode_oneshot_wait)
        position            =   np.asarray(position, dtype=np.float32)
        orientation         =   np.asarray(orientation, dtype=np.float32)
        lin_vel, ang_vel    =   np.asarray(lin_vel, dtype=np.float32), np.asarray(ang_vel, dtype=np.float32)
        if compute_acel == True: lin_acel, ang_acel  =   self.compute_aceleration(lin_vel, ang_vel)
        else: lin_acel, ang_acel =   np.zeros(3, dtype=np.float32), np.zeros(3, dtype=np.float32) 

        return position, orientation, lin_vel, ang_vel, lin_acel, ang_acel

    def compute_aceleration(self, linv, angv):
        assert linv is not None and angv is not None, "linv or angv must not be a none datatype"

        lina = (linv-self.prev_linvel)/self.dt
        anga = (angv-self.prev_angvel)/self.dt

        return lina, anga

    def _getGaussVectorOrientation(self): 
        x = [gauss(0, 0.6) for _ in range(3)]
        return  np.asarray(x, dtype=np.float32)

    def _get_random_pos_ang(self, max_radius = 3.2, max_angle = np.pi, respecto:np.ndarray=None):
        if respecto is None:
            respecto    =   np.zeros(3, dtype=np.float32)

        max_radius_per_axis =   np.sqrt(max_radius * max_radius / 3.0)
        sampledpos          =   np.random.uniform(-max_radius_per_axis, max_radius_per_axis, 3) + respecto
        sampledangle        =   self._getGaussVectorOrientation()

        return sampledpos, sampledangle


