# coding=utf-8
"""The core of the simulator.

A world (instance of the :class:`World` class) consists of bodies (instances
of the :class:`Body` class) interlinked by joints (instances of :class:`Joint`
subclasses). Joints serve two purposes in arboris:

- restrict the relative motion between bodies (for instance a hinge joint only
  allows for rotations around its axis)
- and parametrize the bodies positions (for instance a single angle is enough
  to parametrize the relative position of two bodies constrained by a hinge
  joint).

A world forms an oriented tree whose nodes are the bodies and edges are the
joints, so that the state (pose and velocity) of each body can be computed from
the state of its parent joints (the joints on the path from the body to the
root of the tree (which is body called "ground")).

One or more frames (instances of the :class:`SubFrame` class) can be associated
to bodies and serve as anchor points to the joints.

"""
__author__ = ("Sébastien BARTHÉLEMY <barthelemy@crans.org>")

from abc import ABCMeta, abstractmethod, abstractproperty

from numpy import array, zeros, eye, dot, arange
import numpy

import arboris.homogeneousmatrix as Hg
from arboris.rigidmotion import RigidMotion
from arboris.massmatrix import ismassmatrix

import warnings

def simplearm():
    from arboris.robots.simplearm import add_simplearm
    w = World()
    add_simplearm(w)
    return w


class NamedObject(object):
    """
    A class for anything named to depend from.
    """

    def __init__(self, name=None):
        self.name = name

    def __repr__(self):
        if self.name is None:
            return object.__repr__(self)
        else:
            return '<{0}.{1} object named "{2}" at "{3}")>'.format(
                self.__class__.__module__,
                self.__class__.__name__,
                self.name,
                hex(id(self)))

class NamedObjectsList(list):
    """A list of (possibly) NamedObjects.

    This class behaves like the built-in ``list`` class except that
    objects can be retrieved by name, using a string index or the
    ``find`` method. In the first case, the first matching object is
    returned, in the second case a (raw) list of matching objects is
    returned.

    **Example:**

    >>> L = [Body(name="Jean-Paul"), 1., 'sss']
    >>> NL = NamedObjectsList(L)
    >>> NL.append(Body(name=u'Guard'))
    >>> NL.append(Body(name=u'Jean-Paul'))
    >>> NL['Guard'] #doctest: +ELLIPSIS
    <arboris.core.Body object named "Guard" at "0x...")>
    >>> NL['Jean-Paul'] #doctest: +ELLIPSIS
    <arboris.core.Body object named "Jean-Paul" at "0x...")>
    >>> NL.find('Jean-Paul') #doctest: +ELLIPSIS
    [<arboris.core.Body object named "Jean-Paul" at "0x...")>, <arboris.core.Body object named "Jean-Paul" at "0x...")>]
    >>> NL['Jean-Marie']
    Traceback (most recent call last):
        ...
    KeyError: 'No object named "Jean-Marie".'
    >>> NL['Jean-Paul'] = 3
    Traceback (most recent call last):
        ...
    TypeError: list indices must be integers, not str

    """
    def __init__(self, iterable=None):
        list.__init__(self)
        if iterable is None:
            iterable = tuple()
        self.extend(iterable)

    def find(self, name):
        result = []
        for obj in self:
            if isinstance(obj, NamedObject) and (obj.name == name):
                result.append(obj)
        return result

    def __getitem__(self, index):
        if isinstance(index, str):
            # returns the first item whose name is matching
            for obj in self:
                if isinstance(obj, NamedObject) and (obj.name == index):
                    return obj
            raise KeyError('No object named "{0}".'.format(index))
        else:
            return list.__getitem__(self, index)

    def as_dict(self):
        """Returns a dictionary whose keys are the objects names and
        values are the objects themselves. Objects with no name are
        ignored. Duplicate names raise a ``DuplicateName``
        exception.

        """
        result = {}
        for obj in self:
            if isinstance(obj, NamedObject) and obj.name is not None:
                if obj.name not in result:
                    result[obj.name] = obj
                else:
                    raise DuplicateNameError()
        return result


class DuplicateNameError(Exception):
    def __init__(self, message):
        Exception.__init__(self)
        self.message = message

    def __str__(self):
        return self.message


def name_all_elements(nlist, check_unicity=False):
    # give a unique name for all unnamed elements
    for elem in nlist: 
        if elem.name is None:
            elem.name = str(id(elem))
    # check duplicate name
    if check_unicity is True:
        list_name = [e.name for e in nlist]
        set_name  = set(list_name)
        if len(list_name) != len(set_name):
            for e in set_name:
                list_name.remove(e)
            raise DuplicateNameError("Some names are duplicated:\n" +
                                     str(list_name))


class Frame(object):
    """A generic class for frames.
    """
    __metaclass__ = ABCMeta

    @abstractproperty
    def pose(self):
        pass

    @abstractproperty
    def jacobian(self):
        pass

    @abstractproperty
    def djacobian(self):
        pass

    @abstractproperty
    def twist(self):
        pass

    @abstractproperty
    def body(self):
        pass

    @abstractproperty
    def bpose(self):
        pass

class Joint(RigidMotion, NamedObject):
    """A generic class for ideal joints.

    An ideal joint is a kinematic restriction of the allowed relative
    twist of two frames, here named frames 0 and 1, to which the joint
    is said to be attached. In arboris, it serves to parametrize the
    relative position and velocity of frame 1 regarding to frame 0.

    This class has virtual methods and properties. It should be
    subclassed by concrete implementations.

    """
    __metaclass__ = ABCMeta

    def __init__(self, name=None):
        RigidMotion.__init__(self)
        NamedObject.__init__(self, name)
        self._frame0 = None
        self._frame1 = None
        self._dof = None # will be set by World.
        self.gpos = zeros((0, 0))
        self.gvel = zeros(0)

    def set_frames(self, frame0, frame1):
        self._frame0 = frame0
        self._frame1 = frame1

    @abstractproperty
    def ndof(self):
        """Number of degrees of freedom of the joint.
        """
        pass

    @property
    def dof(self):
        if self._dof is None:
            raise ValueError("joint dof is None. run world.init() first.")
        else:
            return self._dof

    @property
    def frames(self):
        """Frames to which the joint is attached.
        """
        return (self._frame0, self._frame1)

    @property
    def frame0(self):
        return self._frame0

    @property
    def frame1(self):
        return self._frame1

    @property
    def twist(self):
        r"""Relative twist `\twist[1]_{1/0}`
        
        It is the twist of frame 1 regarding to frame 0 expressed in frame 1
        """
        return dot(self.jacobian, self.gvel)

    @abstractproperty
    def jacobian(self):
        r"""Jacobian of the joint relative position.

        Return the matrix `J` such that `\twist[1]_{1/0} = J \nu`.
        Or, with numpy ntation, ``j.twist == dot(j.jacobian, j.gvel)``.

        This matrix generally changes with the joint generalized position.
        """
        pass

    @abstractproperty
    def djacobian(self):
        pass

    @abstractmethod
    def integrate(self, gvel, dt):
        pass


class LinearConfigurationSpaceJoint(Joint):
    """
    Joints whose configuration space is diffeomorph to R^ndof.
    """
    def __init__(self, gpos=None, gvel=None, name=None):
        Joint.__init__(self, name)
        if gpos is None:
            self.gpos = zeros(self.ndof)
        else:
            self.gpos = array(gpos).reshape((self.ndof))
        if gvel is None:
            self.gvel = zeros(self.ndof)
        else:
            self.gvel = array(gvel).reshape((self.ndof))

    def integrate(self, gvel, dt):
        self.gvel = gvel
        self.gpos += dt * self.gvel


class JointsList(NamedObjectsList):
    def __init__(self, iterable):
        NamedObjectsList.__init__(self, iterable)
        self._init_dof()

    def _init_dof(self):
        self._dof = slice(0, 0)
        for obj in self:
            if isinstance(obj, Joint):
                assert obj.dof.step in (None, 1)
                if isinstance(self._dof, slice):
                    if (self._dof.stop == obj.dof.start):
                        assert self._dof.step in (None, 1)
                        # if possible, keep self._dof as a slice
                        self._dof = slice(self._dof.start, obj.dof.stop)
                    else:
                        # otherwise, self._dof is converted into a sequence
                        self._dof = arange(self._dof.start, self._dof.stop).tolist()
                        self._dof.extend(arange(obj.dof.start, obj.dof.stop))
                else:
                    self._dof.extend(arange(obj.dof.start, obj.dof.stop))

    @property
    def dof(self):
        return self._dof

class Constraint(NamedObject):
    __metaclass__ = ABCMeta

    def __init__(self, name=None):
        NamedObject.__init__(self, name)
        self._is_enabled = True
        self._force = zeros(0)
        self._child_obj_to_reg = []

    def is_enabled(self):
        return self._is_enabled

    def enable(self):
        self._is_enabled = True

    def disable(self):
        self._is_enabled = False

    @abstractmethod
    def init(self, world):
        pass

    @property
    def gforce(self):
        return dot(self.jacobian.T, self._force)

    @abstractproperty
    def jacobian(self):
        pass

    @abstractproperty
    def ndol(self):
        """Number of degrees of "liaison"

        In french: *nombre de degrés de liaison*. This is equal to (6 - ndof).
        """
        pass

    @abstractmethod
    def update(self, dt):
        pass

    @abstractmethod
    def is_active(self):
        pass

    @abstractmethod
    def solve(self, vel, admittance, dt):
        pass


class Shape(NamedObject):
    """A generic class for geometric shapes used in collision detection
    """
    def __init__(self, frame, name=None):
        assert isinstance(frame, Frame)
        self.frame = frame
        NamedObject.__init__(self, name)


class Controller(NamedObject):
    __metaclass__ = ABCMeta

    def __init__(self, name=None):
        NamedObject.__init__(self, name)

    @abstractmethod
    def init(self, world):
        pass

    @abstractmethod
    def update(self, dt):
        pass


class World(NamedObject):
    """

    """

    def __init__(self, name=None):
        NamedObject.__init__(self, name)
        self.ground = Body('ground')
        self._current_time = 0.
        self._up = array((0., 1., 0.))
        self._controllers = []
        self._constraints = []
        self._subframes   = []
        self._shapes      = []
        self._ndof = 0
        self._gvel = array([])
        self._mass =  array([])# updated by self.update_dynamic()
        self._gforce =  array([])
        self._viscosity = array([]) # updated by self.update_dynamic()
        self._nleffects = array([]) # updated by self.update_dynamic()
        self._impedance = array([]) # updated by self.update_controller()
        self._admittance = array([]) # updated by self.update_controller()

    def iterbodies(self):
        """Iterate over all bodies, with a depth-first strategy."""
        yield self.ground
        for b in self.ground.iter_descendant_bodies():
            yield b

    def getbodies(self):
        """Returns a NamedObjectsList of the world bodies.
        """
        return NamedObjectsList(self.iterbodies())

    def iterconstraints(self):
        """Iterate over all constraints."""
        for obj in self._constraints:
            yield obj

    def getconstraints(self):
        """Returns a NamedObjectsList of the world constraints"""
        return NamedObjectsList(self._constraints)

    def getcontrollers(self):
        """Returns a NamedObjectsList of the world controllers"""
        return NamedObjectsList(self._controllers)

    def itersubframes(self):
        """Iterate over all subframes."""
        for obj in self._subframes:
            yield obj

    def itermovingsubframes(self):
        """Iterate over all subframes."""
        for obj in self._subframes:
            if isinstance(obj, MovingSubFrame):
                yield obj

    def iterframes(self):
        """Iterate over all frames (bodies and subframes)."""
        for obj in self.iterbodies():
            yield obj
        for obj in self.itersubframes():
            yield obj

    def getframes(self):
        """Returns a NamedObjectsList of the world frames.
        """
        frames = self.getbodies()
        frames.extend(self._subframes)
        return frames

    def itershapes(self):
        """Iterate over all shapes."""
        for obj in self._shapes:
            yield obj

    def getshapes(self):
        """Returns a NamedObjectsList of the world shapes.
        """
        return NamedObjectsList(self._shapes)

    def iterjoints(self):
        """Iterate over all joints, with a depth-first strategy."""
        for j in self.ground.iter_descendant_joints():
            yield j

    def getjoints(self):
        """Returns a JointsList of the joints.

        **Example:**

            >>> w = simplearm()
            >>> joints = w.getjoints()
            >>> joints[1] #doctest: +ELLIPSIS
            <arboris.joints.RzJoint object named "Elbow" at "0x...")>
            >>> joints['Elbow'] #doctest: +ELLIPSIS
            <arboris.joints.RzJoint object named "Elbow" at "0x...")>
            >>> joints.dof
            slice(0, 3, None)

        """
        return JointsList(self.iterjoints())

    def add_link(self, frame0, joint, frame1, *args):
        """Add a link (ie. Joint and Body) to the world.

        :param frame0: the frame where the new joint will be attached.
                       It should belong to a bady which is already in the world.
        :param joint: the joint to be added.
        :param frame1: the frame of the body which is to be added to the world.

        One can also add several links at a time::

            world.add_link(frame0_a,joint_a,frame1_a,frame0_b,joint_b,frame1_b)

        """
        assert isinstance(frame0, Frame)
        assert isinstance(frame1, Frame)
        assert isinstance(joint, Joint)
        assert joint.frame0 is None
        assert joint.frame1 is None
        assert len(args) % 3 == 0
        joint.set_frames(frame0, frame1)
        if frame1.body.parentjoint is None:
            frame1.body.parentjoint = joint
        else:
            raise ValueError("frame1's body already has a parent joint, " + \
            "which means you're probably trying to create a kinematic loop." + \
            "Try using a constraint instead.")
        frame0.body.childrenjoints.append(joint)
        self.register(frame0)
        self.register(frame1)
        if len(args) > 0:
            self.add_link(*args)

    def replace_joint(self, old_joint, *args):
        """Replace a joint by another joint or by another kinematic chain.

        **Syntax:** ::

            world.replace_joint(old_joint, new_joint)
            world.replace_joint(old_joint, frame0, new_joint, ... , frame1)

        **Example:**

            >>> w = simplearm()
            >>> from arboris.joints import RzJoint
            >>> joints = w.getjoints()
            >>> w.replace_joint(joints['Elbow'], RzJoint())

        """
        assert isinstance(old_joint, Joint)
        assert old_joint in old_joint.frame0.body.childrenjoints
        assert old_joint is old_joint.frame1.body.parentjoint
        if len(args) == 1:
            new_joint = args[0]
            frame0 = old_joint.frame0
            frame1 = old_joint.frame1
            self.replace_joint(old_joint, frame0, new_joint, frame1)
        elif len(args) == 0:
            raise RuntimeError("nb args is 0, need at least the new joint.")
        elif len(args) % 3 != 0:
            raise RuntimeError("args not a multiple of 3. Template should be"+ \
            "(old_joint, ..., child_frame, parent_frame, new_joint, ...)")
        else:
            body0 = args[0].body
            body1 = args[-1].body
            assert old_joint.frame0.body is body0
            assert old_joint.frame1.body is body1
            body1.parentjoint = None
            old_joint.set_frames(None, None)
            self.add_link(*args)
            # new_joint is now the last in body0.childrenjoint
            # and old_joint is still somewhere in body0.childrenjoint
            # let's fix that
            i = body0.childrenjoints.index(old_joint)
            body0.childrenjoints[i] = body0.childrenjoints.pop()
            self.init()

    def register(self, obj):
        """
        Register an object into the world.

        ``obj`` can be a subframe, a shape or a constraint.

        :arguments:
            obj
                the object that will be added. It may be a subframe, a shape,
                a joint, a body or a controller.

        **Example:**

        >>> w = simplearm()
        >>> from arboris.controllers import ProportionalDerivativeController
        >>> joints = w.getjoints()
        >>> c0 = ProportionalDerivativeController(joints[1:3],
        ...     name = 'my controller')
        >>> w.register(c0)
        >>> c1 = ProportionalDerivativeController(joints[0:1])
        >>> w.register(c1)
        >>> w.init()

        """
        if isinstance(obj, Body):
            pass
        elif isinstance(obj, Joint):
            raise ValueError("Joints should not be registered. " + \
                             "Use add_link() instead.")
        elif isinstance(obj, SubFrame) or isinstance(obj, MovingSubFrame):
            if not obj in self._subframes:
                self._subframes.append(obj)
        elif isinstance(obj, Shape):
            if not obj in self._shapes:
                self._shapes.append(obj)
            self.register(obj.frame)
        elif isinstance(obj, Constraint):
            if not obj in self._constraints:
                self._constraints.append(obj)
                for child_obj in obj._child_obj_to_reg:
                    self.register(child_obj)
        elif isinstance(obj, Controller):
            if not obj in self._controllers:
                self._controllers.append(obj)
        else:
            raise ValueError("I do not know how to register objects " + \
                             "of type {0}".format(type(obj)))

    def parse(self, target):
        """Parse the world and call hooks on the ``target`` object.

        This helper function  walks the world with a depth-first strategy and
        call the ``register`` and ``add_link`` methods of the ``target``
        object. Each world element will be register only once. The order of the
        successive registration is meant to ease conversion to hierachic
        data structures such as collada or OSG ones.

        TODO: add an example, such as the parse of the pantograph.

        """
        def _register_frame_and_children(frame):
            """register a frame and its children (except joints).

            Register the principal frame and its subframes and shapes.
            """
            if not frame in registered:
                registered.add(frame)
                target.register(frame)
                if isinstance(frame, Body):
                    for f in self._subframes:
                        if not(f in registered) and (frame is f.body):
                            _register_frame_and_children(f)
                for s in self._shapes:
                    if frame is s.frame:
                        target.register(s)

        def _register_constraints():
            for c in self._constraints:
                target.register(c)

        def _register_controllers():
            for c in self._controllers:
                target.register(c)

        def _recurse_over_joints(children_joints):
            for j in children_joints:
                (f0, f1) = j.frames
                target.add_link(f0, j, f1)
                _register_frame_and_children(f1.body)
                _recurse_over_joints(f1.body.childrenjoints)
        registered = set()
        target.init_parse(self.ground, self.up, self.current_time)
        _register_frame_and_children(self.ground)
        _recurse_over_joints(self.ground.childrenjoints)
        _register_constraints()
        _register_controllers()

    def init(self):
        """ Init the joints-space model of the world.
        """
        self._ndof = 0
        for j in self.iterjoints():
            old_ndof = self._ndof
            self._ndof += j.ndof
            j._dof = slice(old_ndof, self._ndof)

        # Adjust the size of the worldwide model matrices
        # (we wipe their content)
        self._mass = zeros((self._ndof, self._ndof))
        self._nleffects =  zeros((self._ndof, self._ndof))
        self._viscosity = zeros((self._ndof, self._ndof))
        self._gforce = zeros(self._ndof)

        # Init the worldwide generalized velocity vector:
        self._gvel = zeros(self._ndof)
        for j in self.iterjoints():
            self._gvel[j.dof] = j.gvel[:]
            j.gvel = self._gvel[j.dof]

        for c in self._constraints:
            c.init(self)

        for a in self._controllers:
            a.init(self)

    @property
    def current_time(self):
        return self._current_time

    @property
    def up(self):
        return self._up

    @property
    def mass(self):
        return self._mass

    @property
    def viscosity(self):
        return self._viscosity

    @property
    def nleffects(self):
        return self._nleffects

    @property
    def ndof(self):
        return self._ndof

    @property
    def gvel(self):
        return self._gvel.copy()

    @property
    def gforce(self):
        return self._gforce.copy()

    @property
    def admittance(self):
        return self._admittance.copy()

    def update_geometric(self):
        """Compute the forward geometric model.

        This will recursively update each body pose attribute.

        Example:

        >>> w = simplearm()
        >>> w.update_geometric()

        """
        self.ground.update_geometric(eye(4))

    def update_dynamic(self):
        r"""Compute the forward geometric, kinematic and dynamic models.

        Recursively update each body pose, jacobian, djacobian, twist
        and nleffects attributes (thanks to the
        :meth:`arboris.Body.update_dynamic` method) and then update
        the world mass, viscosity and nleffects attributes.

        **Example:**

        >>> w = simplearm()
        >>> w.update_dynamic()


        **Algorithm:**

        The (world) generalized mass, viscosity and nleffect matrices
        respectively denoted `M`, `B` and `N` are computed from each body
        `b` jacobian `\J[b]_{b/g}`, hessian `\dJ[b]_{b/g}`, mass `M_b`,
        viscosity `B_b` and nleffects `N_b` matrices as :

        .. math::
            M &= \sum_b \J[b]_{b/g}\tp \; M_b \; \J[b]_{b/g} \\
            B &= \sum_b \J[b]_{b/g}\tp \; B_b \; \J[b]_{b/g} \\
            N &= \sum_b \J[b]_{b/g}\tp
            \left( M_b \; \dJ[b]_{b/g} + N_b \; \J[b]_{b/g}\right)

        If there is no additional constraint (such as contacts) nor actuation
        involved, the resulting (free) model is then:

        .. math::
            M \dGVel + \left( N + B \right) \GVel = 0

        """
        self.ground.update_dynamic(
            eye(4),
            zeros((6,self._ndof)),
            zeros((6,self._ndof)),
            zeros(6))

        self._mass[:] = 0.
        self._viscosity[:] = 0.
        self._nleffects[:] = 0.
        for b in self.ground.iter_descendant_bodies():
            self._mass += dot(
                dot(b.jacobian.T, b.mass),
                b.jacobian)
            self._viscosity += dot(
                dot(b.jacobian.T, b.viscosity),
                b.jacobian)
            self._nleffects += dot(
                b.jacobian.T,
                dot(b.mass, b.djacobian) + dot(b.nleffects, b.jacobian))

    def update_controllers(self, dt):
        r"""

        :param dt:
        :type dt: float
        :param t:
        :type t: float

        **Example:**

        >>> w = simplearm()
        >>> from arboris.controllers import ProportionalDerivativeController
        >>> joints = w.getjoints()
        >>> a0 = ProportionalDerivativeController(joints[1:2], 2.)
        >>> w.register(a0)
        >>> w.init()
        >>> w.update_dynamic()
        >>> w.update_controllers(0.001)
        >>> w._impedance
        array([[ 686.98833333,  223.44666667,   20.67333333],
               [ 223.44666667,   93.44866667,   10.67333333],
               [  20.67333333,   10.67333333,    2.67333333]])
        >>> w._admittance
        array([[ 0.00732382, -0.0203006 ,  0.0244142 ],
               [-0.0203006 ,  0.07594182, -0.14621124],
               [ 0.0244142 , -0.14621124,  0.76901683]])

        **Algorithm:**

        Let's consider the following discretized 2nd-order model:

        .. math::
            M(t) \dGVel(t+dt) + \left( N(t)+B(t) )\right) \GVel(t+dt) &=
            \GForce(t)

        considering

        .. math::
            \dGVel(t+dt) = \frac{\GVel(t+dt) - \GVel(t)}{dt}

        we get

        .. math::
            \left( \frac{M(t)}{dt}+N(t)+B(t) \right) \GVel(t+dt) &=
            \frac{M(t)}{dt} \GVel(t) + \GForce(t)

        Here `\GForce(t)` sums up the generalized forces due to
        all the active controllers and constraints.

        The generalized force due to a controller has the following form:

        .. math::
            \GForce_a(t) &= \GForce_{0a}(t) + Z_a(t) \GVel(t+td)

        where `\GForce_{0a}(t)` is constant during the
        `[t, t+dt]` period of time.

        It leads us to

        .. math::
            \left( \frac{M(t)}{dt}+N(t)+B(t) -\sum_a Z_a(t) \right)
            \GVel(t+dt) &=
            \frac{M(t)}{dt} \GVel(t) + \sum_a \GForce_{0a}(t)

        One can the define impedance (`Z`) and admittance (`Y`)
        matrices:

        .. math::
            Z(t) &= \frac{M(t)}{dt}+N(t)+B(t)-\sum_a Z_a(t) \\
            Y(t) &= Z^{-1}(t)


        TODO: check the two last tests results!

        """
        assert dt > 0
        self._gforce[:] = 0.
        self._impedance = self._mass/dt + self._viscosity + self._nleffects
        for a in self._controllers:
            (gforce, impedance) = a.update(dt)
            self._gforce += gforce
            self._impedance -= impedance
        self._admittance = numpy.linalg.inv(self._impedance)

    def update_constraints(self, dt, maxiters=1000, tol=1e-4):
        r"""
        In accordance with the integration scheme, we assume the following
        first order model between generalized velocities and generalized
        forces:

        .. math::
            \GVel(t+dt) &= Y(t)
            \left( \frac{M(t)}{dt} \GVel(t) + \GForce(t) \right)

        where:

        - the admittance matrix  `Y` takes into account a
          first order model of the actuators,

        - the actuators generalized forces `\GForce(t)`
          are assumed to be constant during the `[t , t+dt ]`
          time interval.

        This (constraint-free) model must be completed by constraints
        forces `\pre[c]f`, which are mapped to generalized forces
        by the constraint jacobian `\pre[c]J_c\tp`:

        .. math::
            \GVel(t+dt)
            &= Y(t)
            \left( \frac{M(t)}{dt} \GVel(t) + \GForce(t)
                + \sum_{c} \; \pre[c]J_{c}\tp(t) \; \pre[c]f(t)
            \right)

        one can also define the constraint velocity  as:
        `\pre[c]v = \pre[c]J_c \; \GVel` so that:

        .. math::
            \pre[c]v(t+dt)
            &= \pre[c]J_c(t) \; \GVel(t+dt)\\
            &= \pre[c]J_c(t) \; Y(t)
            \left(
                \frac{M(t)}{dt} \GVel(t) + \; \GForce(t)
            \right)
            + \sum_d \; \pre[c]J_c(t) \; Y(t) \; \pre[d]J_d\tp(t)
            \; \pre[d]f(t)

        one can define the (global) constraints velocity `v'`,
        force `f'`, jacobian matrix `J'`
        and admittance matrix `Y'`:

        .. math::
            J'(t) &=
            \begin{bmatrix}
                \pre[0]J_0(t)\\
                \vdots\\
                \pre[c]J_c(t)\\
                \vdots
            \end{bmatrix}\\
            v'(t) &=
            \begin{bmatrix}
                \pre[0]v(t)\\ \vdots \\ \pre[c]v(t) \\ \vdots
            \end{bmatrix}\\
            f'(t) &=
            \begin{bmatrix}
                \pre[0]f(t)\\ \vdots \\ \pre[c]f(t) \\ \vdots
            \end{bmatrix}\\
            Y'(t) &=
            J'(t) \; Y(t) \; J'(t)\tp

        and get a synthetic expression:

        .. math::
            v'(t+dt) &= J'(t) \; Y(t)
            \left( \frac{M(t)}{dt}  \GVel(t) + \GForce(t) \right)
            + Y'(t) \; f'


        This method computes the constraint forces in three steps:

        - ask each active constraint object for its jacobian,

        - compute `J`, `v`  and `Y`,

        - iterate over each constraint object in order to compute
          `\pre[c]f`. At each iteration the force is
          updated by `\Delta\pre[c]f`

        - eventually add each active constraint generalized force to
          world :attr:`~arboros.core.World._gforce` property.

        TODO: add an example.

        """
        assert dt > 0
        constraints = []
        ndol = 0
        for c in self._constraints:
            if c.is_enabled():
                c.update(dt)
                if c.is_active():
                    c._dol = slice(ndol, ndol+c.ndol)
                    ndol = ndol + c.ndol
                    constraints.append(c)
        jac = zeros((ndol, self._ndof))
        gforce = self._gforce.copy()
        for c in constraints:
            jac[c._dol, :] = c.jacobian
            gforce += c.gforce
        vel = dot(jac, dot(self._admittance,
                           dot(self._mass, self._gvel/dt) + gforce))
        admittance = dot(jac, dot(self._admittance, jac.T))

        previous_vel = vel.copy()
        for k in arange(maxiters):
            for c in constraints:
                dforce = c.solve(vel[c._dol], admittance[c._dol, c._dol], dt)
                vel += dot(admittance[:, c._dol], dforce)
            error = numpy.linalg.norm(vel - previous_vel)
            if k > 0 and error < tol:
                break
            previous_vel = vel.copy()
        for c in constraints:
            self._gforce += c.gforce

    def integrate(self, dt):
        r"""

        From the :meth:`update_controllers` and
        :meth:`update_constraints` methods we get the new
        generalized velocity.

        .. math::
            \GVel(t+dt) &= Y(t) \left(
            \frac{M(t)}{dt} \GVel(t)
            + \sum_{c=\text{controllers}} \GForce_{0a}(t)
            + \sum_{c=\text{constraints}} \GForce_{c}(t)
            \right)

        In order to get the new generalized position, each joint is integrated
        separately.

        TODO: add support for kinematic controllers
        TODO: repair this doctest
        TODO: check the last test result!

        >> w = simplearm()
        >> joints = w.getjoints()
        >> joints['Shoulder'].gpos[:] = -1.
        >> from arboris.controllers import ProportionalDerivativeController
        >> c0 = ProportionalDerivativeController(w.joints[1:2], 1.)
        >> w.register(c0)
        >> w.init()
        >> w.update_dynamic()
        >> dt = 0.001
        >> w.update_controllers(dt)
        >> w.integrate(dt)
        >> w._gvel
        array([-0.00709132,  0.03355273, -0.09131555])
        """
        assert dt > 0
        self._gvel[:] = dot(self._admittance,
                            dot(self._mass, self._gvel/dt) + self._gforce)

        for j in self.iterjoints():
            j.integrate(self._gvel[j.dof], dt)
        self._current_time += dt


    def name_all_elements(self, check_unicity=False):
        for wlist in [self.getjoints(), self.getframes(), self.getshapes(),
                      self.getconstraints(), self.getcontrollers()]:
            name_all_elements(wlist, check_unicity)



class _SubFrame(NamedObject, Frame):

    def __init__(self, body, bpose=None, name=None):
        """Create a frame rigidly fixed to a body.

        >>> b = Body()
        >>> f = SubFrame(b, Hg.rotz(3.14/3.),'Brand New Frame')

        The ``body`` argument must be a member of the ``Body`` class:
        >>> f = SubFrame(None, Hg.rotz(3.14/3.))
        Traceback (most recent call last):
            ...
        ValueError: The ``body`` argument must be an instance of the ``Boby`` class

        The ``bpose`` argument must be an homogeneous matrix:
        >>> b = Body()
        >>> from numpy import ones
        >>> f = SubFrame(b, ones((4,4)))
        Traceback (most recent call last):
            ...
        AssertionError

        """
        NamedObject.__init__(self, name)
        Frame.__init__(self)
        
        if bpose is None:
            bpose = eye(4)

        assert Hg.ishomogeneousmatrix(bpose)
        self._bpose = bpose
        if not(isinstance(body, Body)):
            raise ValueError(
            "The ``body`` argument must be an instance of the ``Boby`` class")
        else:
            self._body = body

    @property
    def pose(self):
        try:
            return dot(self._body.pose, self._bpose)
        except TypeError:
            raise TypeError("pose is not up to date, run world.update_geometric() first.")

    @property
    def twist(self):
        try:
            return dot(Hg.iadjoint(self._bpose), self._body.twist)
        except TypeError:
            raise TypeError("twist is not up to date, run world.update_dynamic() first.")

    @property
    def jacobian(self):
        try:
            return dot(Hg.iadjoint(self._bpose), self._body.jacobian)
        except TypeError:
            raise TypeError("jacobian is not up to date, run world.update_dynamic() first.")

    @property
    def djacobian(self):
        try:
            # we assume self._bpose is constant
            return dot(Hg.iadjoint(self._bpose), self._body.djacobian)
        except TypeError:
            raise TypeError("djacobian is not up to date, run world.update_dynamic() first.")

    @property
    def body(self):
        return self._body


class SubFrame(_SubFrame):
    @property
    def bpose(self):
        return self._bpose.copy()


class MovingSubFrame(_SubFrame):
    @property
    def bpose(self):
        return self._bpose.copy()

    @bpose.setter
    def bpose(self, bpose):
        assert Hg.ishomogeneousmatrix(bpose)
        self._bpose[:] = bpose

class Body(NamedObject, Frame):

    def __init__(self, name=None, mass=None, viscosity=None):
        NamedObject.__init__(self, name)
        Frame.__init__(self)
        if mass is None:
            mass = zeros((6, 6))
        else:
            assert(ismassmatrix(mass, True))
        if viscosity is None:
            viscosity = zeros((6, 6))
        else:
            pass #TODO: check the viscosity matrix

        # the object will have a frame, with the same name as the object itself
        self.parentjoint = None
        self.childrenjoints = []
        self.mass = mass
        self.viscosity = viscosity
        self._pose = None # updated by update_{geometric,kinematic,dynamic}
        self._jacobian = None # updated by update_{geometric,kinematic,dynamic}
        self._djacobian = None # updated by update_dynamic
        self._twist = None # updated by update_dynamic
        self._nleffects = None # updated by update_dynamic

    def iter_descendant_bodies(self):
        """Iterate over all descendant bodies, with a depth-first strategy"""
        for b in map(lambda j: j.frame1.body, self.childrenjoints):
            yield b
            for bb in b.iter_descendant_bodies():
                yield bb

    def iter_ancestor_bodies(self):
        if self.parentjoint is not None:
            parentbody = self.parentjoint.frame0.body
            yield parentbody
            for a in parentbody.iter_ancestor_bodies():
                yield a

    def iter_descendant_joints(self):
        """Iterate over all descendant joints, with a depth-first strategy"""
        for j in self.childrenjoints:
            yield j
            for jj in j.frame1.body.iter_descendant_joints():
                yield jj

    def iter_ancestor_joints(self):
        if self.parentjoint is not None:
            yield self.parentjoint
            for a in self.parentjoint.frame0.body.iter_ancestor_joints():
                yield a

    @property
    def pose(self):
        return self._pose

    @property
    def jacobian(self):
        return self._jacobian

    @property
    def djacobian(self):
        return self._djacobian

    @property
    def twist(self):
        return self._twist

    @property
    def nleffects(self):
        return self._nleffects

    @property
    def bpose(self):
        return eye(4)

    @property
    def body(self):
        return self

    def update_geometric(self, pose):
        """
        - g: ground body
        - p: parent body
        - c: child body
        - r: reference frame of the joint, rigidly fixed to the parent body
        - n: new frame of the joint, rigidly fixed to the child body

        so H_nc and H_pr are constant.

        H_gc = H_gp * H_pc
             = H_gp * (H_pr * H_rn * H_nc)
        """
        self._pose = pose
        H_gp = pose
        for j in self.childrenjoints:
            H_cn = j.frame1.bpose
            H_pr = j.frame0.bpose
            H_rn = j.pose
            H_pc = dot(H_pr, dot(H_rn, Hg.inv(H_cn)))
            child_pose = dot(H_gp, H_pc)
            j.frame1.body.update_geometric(child_pose)

    def update_dynamic(self, pose, jac, djac, twist):
        r"""Compute the body ``pose, jac, djac, twist`` and its children ones.

        This method (1) sets the body dynamical model (pose, jacobian,
        hessian and twist) to the values given as argument, (2) computes
        the dynamical model of the children bodies and (3) call the
        equivalent method on them.

        As a result, the dynamical model of all the bodies is computed
        recursively.

        :param pose: the body pose relative to the ground: `H_{gb}`
        :type pose: 4x4 ndarray
        :param jac: the body jacobian relative to the world (in body frame):
            `\J[b]_{b/g}`
        :type jac: 6x(ndof) ndarray
        :param djac: the derivative of the body jacobian: `\dJ[b]_{b/g}`
        :param twist: the body twist: `\twist[b]_{b/g}`
        :type twist: 6 ndarray

        **Algorithm:**

        Let's define the following notations:

        - `g`: the ground body,
        - `p`: the parent body (which is the present :class:`arboris.Body`
          instance)
        - `c`: a child body,
        - `j`: the joint between the bodies `p` and `c`,
        - `r`: reference frame of the joint `j`, rigidly fixed to the parent
          body
        - `n`: new frame of the joint `j`, rigidly fixed to the child body

        .. image:: img/body_model.png

        One can notice that `H_{nc}` and `H_{pr}` are constant.

        The child body pose can be computed as

        .. math::

            H_{gc} &= H_{gp} \; H_{pc} \\
                   &= H_{gp} \; (H_{pr} \; H_{rn} \; H_{nc})

        where `H_{rn}` depends on the joint generalized configuration and is
        given by its :attr:`~arboris.core.Joint.pose` attribute.

        The chil body twist is given as

        .. math::

            \twist[c]_{c/g} &= \Ad[c]_p \; \twist[p]_{p/g} + \twist[c]_{c/p} \\
            &= \Ad[c]_p \; \twist[p]_{p/g} + \Ad[c]_n \; \twist[n]_{n/r} \\
            &= \Ad[c]_p \; \J[p]_{p/g} \; \GVel
               + \Ad[c]_n \; \J[n]_{n/r} \; \GVel_j \\
            &= \J[c]_{c/g} \; \GVel

        where  `\twist[n]_{n/r}` isgiven by the joint
        :attr:`~arboris.core.Joint.twist` attribute.
        \GVel_j is the generalized velocity of the joint `j` and is
        related to the world generalized velocity by trivial projection

        .. math::
            \GVel_j &=
                \begin{bmatrix}
                    0 & \cdots &0 & I & 0 & \cdots & 0
                \end{bmatrix} \; \GVel

        therefore, the child body jacobian is

        .. math::
            \J[c]_{c/g} &= \Ad[c]_p \; \J[p]_{p/g} +
            \begin{bmatrix}
            0 & \cdots & 0 & \Ad[c]_n \; \J[n]_{n/r} & 0 & \cdots & 0
            \end{bmatrix} \\

        where `\J[n]_{n/r}` is given by the joint
        :attr:`~arboris.core.Joint.jacobian` attribute. Derivating the previous
        expression leads to the child body acceleration:

        .. math::
            \dtwist[c]_{c/g} &= \dAd[c]_p \; \J[p]_{p/g} \; \GVel
            + \Ad[c]_p \; \dJ[p]_{p/g} \; \GVel
            + \Ad[c]_p \; \J[p]_g \; \dGVel
            + \Ad[c]_n \; \dJ[n]_{n/r} \; \GVel_j
            + \Ad[c]_n \; \J[n]_{m/r} \dGVel_j \\
            &= \J[c]_{c/g} \; \dGVel + \dJ[c]_{c/g} \; \GVel

        the expression of the child body hessian is then obtained by
        identification:

        .. math::
            \dJ[c]_{c/g} \; \GVel
            &= \dAd[c]_p \; \J[p]_{p/g} \; \GVel
            + \Ad[c]_p \; \dJ[p]_{p/g} \; \GVel
            + \Ad[c]_n \; \dJ[n]_{n/r} \; \GVel_j \\

            \dJ[c]_{c/g}
            &= \dAd[c]_p \; \J[p]_{p/g} + \Ad[c]_p \; \dJ[p]_{p/g} +
            \begin{bmatrix}
            0 & \cdots & 0 & (\Ad[c]_n \; \dJ[n]_{n/r}) & 0 & \cdots & 0
            \end{bmatrix}

        with

        .. math::
            \dAd[c]_p &= \Ad[c]_n \; \dAd[n]_r \; \Ad[r]_p

        and where `\dAd[n]_r` and `\dJ[n]_{n/r}` are respectively given by
        the joint :attr:`~arboris.core.Joint.idadjoint` and
        :attr:`~arboris.core.Joint.djacobian` attributes.

        T_ab: velocity of {a} relative to {b} expressed in {a} (body twist)
        """
        self._pose = pose
        self._jacobian = jac
        self._djacobian = djac
        self._twist = twist
        wx = array(
            [[             0, -self.twist[2],  self.twist[1]],
             [ self.twist[2],              0, -self.twist[0]],
             [-self.twist[1],  self.twist[0],              0]])
        if self.mass[5, 5] <= 1e-10: #TODO: avoid hardcoded value
            rx = zeros((3, 3))
        else:
            rx = self.mass[0:3, 3:6]/ self.mass[5,5]
        self._nleffects = zeros((6, 6))
        self._nleffects[0:3, 0:3] = wx
        self._nleffects[3:6, 3:6] = wx
        self._nleffects[0:3, 3:6] = dot(rx, wx) - dot(wx, rx)
        self._nleffects = dot(self.nleffects, self.mass)

        H_gp = pose
        J_pg = jac
        dJ_pg = djac
        T_pg = twist
        for j in self.childrenjoints:
            H_cn = j.frame1.bpose
            H_pr = j.frame0.bpose
            H_rn = j.pose
            H_pc = dot(H_pr, dot(H_rn, Hg.inv(H_cn)))
            child_pose = dot(H_gp, H_pc)
            Ad_cp = Hg.iadjoint(H_pc)
            Ad_cn = Hg.adjoint(H_cn)
            Ad_rp = Hg.adjoint(Hg.inv(H_pr))
            dAd_nr = j.idadjoint
            dAd_cp = dot(Ad_cn, dot(dAd_nr, Ad_rp))
            T_nr = j.twist
            J_nr = j.jacobian
            dJ_nr = j.djacobian
            child_twist = dot(Ad_cp, T_pg) + dot(Ad_cn, T_nr)
            child_jac = dot(Ad_cp, J_pg)
            child_jac[:, j.dof] += dot(Ad_cn, J_nr)

            child_djac = dot(dAd_cp, J_pg) + dot(Ad_cp, dJ_pg)
            child_djac[:, j.dof] += dot(Ad_cn, dJ_nr)
            j.frame1.body.update_dynamic(child_pose, child_jac, child_djac,
                                          child_twist)


class Observer(NamedObject):
    __metaclass__ = ABCMeta

    def __init__(self, name=None):
        NamedObject.__init__(self, name)

    @abstractmethod
    def init(self, world, timeline):
        pass

    @abstractmethod
    def update(self, dt):
        pass

    @abstractmethod
    def finish(self):
        pass



def simulate(world, timeline, observers=()):
    """Run a full simulation,

    :param world: the world to be simulated
    :type world: :class:`arboris.core.World`
    :param time: a list of distinct times
    :type time: iterable

    Example:

    >>> w = simplearm()
    >>> dt = 0.001
    >>> time = numpy.arange(0,0.01,0.001)
    >>> simulate(w, time)

    """
    if isinstance(observers, dict):
        observers_list = observers.values()
    else:
        observers_list = observers

    if world.current_time != timeline[0]:
        warnings.warn('world.current_time != timeline[0]: \
may become a problem if some conditions depend on world.current_time')
    world._current_time = timeline[0]
    world.init()
    for obs in observers_list:
        obs.init(world, timeline)
    for next_time in timeline[1:]:
        dt = next_time - world.current_time
        world.update_dynamic()
        world.update_controllers(dt)
        world.update_constraints(dt)
        for obs in observers_list:
            obs.update(dt)
        world.integrate(dt)
    for obs in observers_list:
        obs.finish()
