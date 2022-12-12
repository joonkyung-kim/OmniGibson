from abc import ABCMeta, abstractmethod
from collections import namedtuple
from omnigibson.systems import *
from omnigibson.objects.dataset_object import DatasetObject
from omnigibson.object_states import *
import omnigibson.utils.transform_utils as T
from omnigibson.utils.usd_utils import BoundingBoxAPI


# Tuple of attributes of objects created in transitions.
_attrs_fields = ["category", "model", "name", "scale", "obj", "pos", "orn"]
ObjectAttrs = namedtuple(
    "ObjectAttrs", _attrs_fields, defaults=(None,) * len(_attrs_fields))

# Tuple of lists of objects to be added or removed returned from transitions.
TransitionResults = namedtuple(
    "TransitionResults", ["add", "remove"], defaults=([], []))


class BaseFilter(metaclass=ABCMeta):
    """Defines a filter to apply to objects."""
    # Class global variable for maintaining cached state
    # Maps tuple of unique filter inputs to cached output value (T / F)
    state = None

    @classmethod
    def __new__(cls, *args, **kwargs):
        """
        Initializes the cached state for this filter if it doesn't already exist
        """
        if cls.state is None:
            cls.state = OrderedDict()

        return super(BaseFilter, cls).__new__(cls)

    @classmethod
    def update(cls):
        """
        Updates the internal state by checking the filter status on all filter inputs
        """
        raise NotImplementedError()

    @abstractmethod
    def __call__(self, obj):
        """Returns true if the given object passes the filter."""
        return False


class CategoryFilter(BaseFilter):
    """Filter for object categories."""

    def __init__(self, category):
        self.category = category

    def __call__(self, obj):
        return obj.category == self.category


class StateFilter(BaseFilter):
    """Filter for object states."""

    def __init__(self, state_type, state_value):
        self.state_type = state_type
        self.state_value = state_value

    def __call__(self, obj):
        if self.state_type not in obj.states:
            return False
        return obj.states[self.state_type].get_value() == self.state_value


class AbilityFilter(BaseFilter):
    """Filter for object abilities."""

    def __init__(self, ability):
        self.ability = ability

    def __call__(self, obj):
        return self.ability in obj._abilities


class OrFilter(BaseFilter):
    """Logical-or of a set of filters."""

    def __init__(self, filters):
        self.filters = filters

    def __call__(self, obj):
        return any(f(obj) for f in self.filters)


class AndFilter(BaseFilter):
    """Logical-and of a set of filters."""

    def __init__(self, filters):
        self.filters = filters

    def __call__(self, obj):
        return all(f(obj) for f in self.filters)


class BaseTransitionRule(metaclass=ABCMeta):
    """
    Defines a set of categories of objects and how to transition their states.
    """

    @abstractmethod
    def __init__(self, individual_filters=None, group_filters=None):
        """
        TransitionRule ctor.

        Args:
            individual_filters (None or dict): Individual object filters that this filter cares about.
                For each name, filter key-value pair, the global transition rule step will produce tuples of valid
                filtered objects such that the cross product over all individual filter outputs occur.
                For example, if the individual filters are:

                    {"apple": CategoryFilter("apple"), "knife": CategoryFilter("knife")},

                the transition rule step will produce all 2-tuples of valid (apple, knife) combinations:

                    {"apple": apple_i, "knife": knife_j}

                based on the current instances of each object type in the scene and pass them to @self.condition as the
                @individual_objects entry.
                If None is specified, then no filter will be applied

            group_filters (None or dict): Group object filters that this filter cares about. For each name, filter
                key-value pair, the global transition rule step will produce a single dictionary of valid filtered
                objects.
                For example, if the group filters are:

                    {"apple": CategoryFilter("apple"), "knife": CategoryFilter("knife")},

                the transition rule step will produce the following dictionary:

                    {"apple": [apple0, apple1, ...], "knife": [knife0, knife1, ...]}

                based on the current instances of each object type in the scene and pass them to @self.condition
                as the @group_objects entry.
                If None is specified, then no filter will be applied
        """
        # Make sure at least one set of filters is specified -- in general, there should never be a rule
        # where no filter is specified
        assert not (individual_filters is None and group_filters is None),\
            "At least one of individual_filters or group_filters must be specified!"

        # Store the filters
        self.individual_filters = dict() if individual_filters is None else individual_filters
        self.group_filters = dict() if group_filters is None else group_filters

    def process(self, individual_objects, group_objects):
        """
        Processes this transition rule at the current simulator step. If @condition evaluates to True, then
        @transition will be executed.

        Args:
            individual_objects (dict): Dictionary mapping corresponding keys from @individual_filters to individual
                object instances where the filter is satisfied. Note: if @self.individual_filters is None or no values
                satisfy the filter, then this will be an empty dictionary
            group_objects (dict): Dictionary mapping corresponding keys from @group_filters to a list of individual
                object instances where the filter is satisfied. Note: if @self.group_filters is None or no values
                satisfy the filter, then this will be an empty dictionary

        Returns:
            2-tuple:
                - bool: Whether @self.condition is met
                - None or TransitionResults: Output from @self.transition (None if it was never executed)
        """
        should_transition = self.condition(individual_objects=individual_objects, group_objects=group_objects)
        return should_transition, \
            self.transition(individual_objects=individual_objects, group_objects=group_objects) \
            if should_transition else None

    @property
    def requires_individual_filters(self):
        """
        Returns:
            bool: Whether this transition rule requires any specific filters
        """
        return len(self.individual_filters) > 0

    @property
    def requires_group_filters(self):
        """
        Returns:
            bool: Whether this transition rule requires any group filters
        """
        return len(self.group_filters) > 0

    @abstractmethod
    def condition(self, individual_objects, group_objects):
        """
        Returns True if the rule applies to the object tuple.

        Args:
            individual_objects (dict): Dictionary mapping corresponding keys from @individual_filters to individual
                object instances where the filter is satisfied. Note: if @self.individual_filters is None or no values
                satisfy the filter, then this will be an empty dictionary
            group_objects (dict): Dictionary mapping corresponding keys from @group_filters to a list of individual
                object instances where the filter is satisfied. Note: if @self.group_filters is None or no values
                satisfy the filter, then this will be an empty dictionary

        Returns:
            bool: Whether the condition is met or not
        """
        pass

    @abstractmethod
    def transition(self, individual_objects, group_objects):
        """
        Rule to apply for each set of objects satisfying the condition.

        Args:
            individual_objects (dict): Dictionary mapping corresponding keys from @individual_filters to individual
                object instances where the filter is satisfied. Note: if @self.individual_filters is None or no values
                satisfy the filter, then this will be an empty dictionary
            group_objects (dict): Dictionary mapping corresponding keys from @group_filters to a list of individual
                object instances where the filter is satisfied. Note: if @self.group_filters is None or no values
                satisfy the filter, then this will be an empty dictionary

        Returns:
            TransitionResults: results from the executed transition
        """
        pass


class GenericTransitionRule(BaseTransitionRule):
    """
    A generic transition rule template used typically for simple rules.
    """

    def __init__(self, individual_filters, group_filters, condition_fn, transition_fn):
        super(GenericTransitionRule, self).__init__(individual_filters, group_filters)
        self.condition_fn = condition_fn
        self.transition_fn = transition_fn

    def condition(self, individual_objects, group_objects):
        return self.condition_fn(individual_objects, group_objects)

    def transition(self, individual_objects, group_objects):
        return self.transition_fn(individual_objects, group_objects)


class SlicingRule(BaseTransitionRule):
    """
    Transition rule to apply to sliced / slicer object pairs.
    """

    def __init__(self):
        # Define an individual filter dictionary so we can track all valid combos of slicer - sliceable
        individual_filters = {ability: AbilityFilter(ability) for ability in ("sliceable", "slicer")}

        # Run super
        super().__init__(individual_filters=individual_filters)

    def condition(self, individual_objects, group_objects):
        slicer_obj, sliced_obj = individual_objects["slicer"], individual_objects["sliceable"]
        slicer_position = slicer_obj.states[Slicer].get_link_position()
        if slicer_position is None:
            return False

        contact_list = slicer_obj.states[ContactBodies].get_value()
        sliced_link_paths = {link.prim_path for link in sliced_obj.links.values()}
        hit_obj = False
        for c in contact_list:
            if not set(c).isdisjoint(sliced_link_paths):
                hit_obj = True
                break
        if not hit_obj:
            return False

        # Slicer may contact the same body in multiple points, so cut once since removing the object from the simulator
        return Sliced in sliced_obj.states and not sliced_obj.states[Sliced].get_value()

    def transition(self, individual_objects, group_objects):
        slicer_obj, sliced_obj = individual_objects["slicer"], individual_objects["sliceable"]
        # Object parts offset annotation are w.r.t the base link of the whole object.
        sliced_obj.states[Sliced].set_value(True)
        pos, orn = sliced_obj.get_position_orientation()

        t_results = TransitionResults()

        # Load object parts.
        for _, part_idx in enumerate(sliced_obj.metadata["object_parts"]):
            # List of dicts gets replaced by {'0':dict, '1':dict, ...}.
            part = sliced_obj.metadata["object_parts"][part_idx]
            part_category = part["category"]
            part_model = part["model"]
            # Scale the offset accordingly.
            part_pos = part["pos"] * sliced_obj.scale
            part_orn = part["orn"]
            part_obj_name = f"{sliced_obj.name}_part_{part_idx}"
            model_root_path = f"{og_dataset_path}/objects/{part_category}/{part_model}"
            usd_path = f"{model_root_path}/usd/{part_model}.usd"

            # Calculate global part pose.
            part_pos = np.array(part_pos) + pos
            part_orn = T.quat_multiply(np.array(part_orn), orn)

            # Circular import.
            from omnigibson.objects.dataset_object import DatasetObject

            part_obj = DatasetObject(
                prim_path=f"/World/{part_obj_name}",
                usd_path=usd_path,
                category=part_category,
                name=part_obj_name,
                scale=sliced_obj.scale,
                abilities={}
            )

            # Add the new object to the results.
            new_obj_attrs = ObjectAttrs(
                obj=part_obj, pos=np.array(part_pos), orn=np.array(part_orn))
            t_results.add.append(new_obj_attrs)

        # Delete original object from stage.
        t_results.remove.append(sliced_obj)

        return t_results


# TODO: Replace with a more standard API when available.
def _contained_objects(scene, container_obj):
    """
    Computes all objects from @scene contained with @container_obj

    Args:
        scene (Scene): Current active scene
        container_obj (BaseObject): Object to find contained objects for

    Returns:
        list of BaseObject: All objects ``inside'' the container object, as defined by its AABB.
    """
    bbox = BoundingBoxAPI.compute_aabb(container_obj.prim_path)
    contained_objs = []
    for obj in scene.objects:
        if obj == container_obj:
            continue
        if BoundingBoxAPI.aabb_contains_point(obj.get_position(), bbox):
            contained_objs.append(obj)
    return contained_objs


class ContainerRule(BaseTransitionRule):
    """
    Rule to apply to a container and a set of objects that may be inside.
    """

    def __init__(self, trigger_steps, final_obj_attrs, container_filter, *contained_filters):
        # Should be in this order to have the container object come first.
        super(ContainerRule, self).__init__((container_filter, *contained_filters))
        self.obj_attrs = final_obj_attrs
        self.trigger_steps = trigger_steps
        self._current_steps = 1
        self._counter = 0

    def condition(self, container_obj, *contained_objs):
        if (
            ToggledOn in container_obj.states and
            not container_obj.states[ToggledOn].get_value()
        ):
            return False
        # Check all objects inside the container against the expected objects.
        all_contained_objs = _contained_objects(og.sim.scene, container_obj)
        contained_prim_paths = set(obj.prim_path for obj in contained_objs)
        all_contained_prim_paths = set(obj.prim_path for obj in all_contained_objs)
        if contained_prim_paths != all_contained_prim_paths:
            return False
        # Check if the trigger step has been reached.
        if self._current_steps < self.trigger_steps:
            self._current_steps += 1
            return False
        self._current_steps = 1
        return True

    def transition(self, container_obj, *contained_objs):
        t_results = TransitionResults()

        # Create a new object to be added.
        all_pos, all_orn = [], []
        for contained_obj in contained_objs:
            pos, orn = contained_obj.get_position_orientation()
            all_pos.append(pos)
            all_orn.append(orn)

        category = self.obj_attrs.category
        model = self.obj_attrs.model
        name = f"{self.obj_attrs.name}_{self._counter}"
        self._counter += 1
        scale = self.obj_attrs.scale

        model_root_path = f"{og_dataset_path}/objects/{category}/{model}"
        usd_path = f"{model_root_path}/usd/{model}.usd"

        final_obj = DatasetObject(
            prim_path=f"/World/{name}",
            usd_path=usd_path,
            category=category,
            name=f"{name}",
            scale=scale)

        final_obj_attrs = ObjectAttrs(
            obj=final_obj, pos=np.mean(all_pos, axis=0), orn=np.mean(all_orn, axis=0))
        t_results.add.append(final_obj_attrs)

        # Delete all objects inside the container.
        for contained_obj in contained_objs:
            t_results.remove.append(contained_obj)

        # Turn off the container, otherwise things would turn into garbage.
        if ToggledOn in container_obj.states:
            container_obj.states[ToggledOn].set_value(False)
        print(f"Applied {ContainerRule.__name__} to {container_obj}")
        return t_results


class ContainerGarbageRule(BaseTransitionRule):
    """
    Rule to apply to a container to turn what remain inside into garbage.

    This rule is used as a catch-all rule for containers to turn objects inside
    the container that did not match any other legitimate rules all into a
    single garbage object.
    """

    def __init__(self, garbage_obj_attrs, container_filter):
        """Ctor for ContainerGarbageRule.

        Args:
            garbage_obj_attrs: (ObjectAttrs) a namedtuple containing the
                attributes of garbage objects to be created.
            container_filter: (BaseFilter) a filter for the container.
        """
        super(ContainerGarbageRule, self).__init__((container_filter,))
        self.obj_attrs = garbage_obj_attrs
        self._cached_contained_objs = None
        self._counter = 0

    def condition(self, container_obj):
        if (
            ToggledOn in container_obj.states and
            not container_obj.states[ToggledOn].get_value()
        ):
            return False
        self._cached_contained_objs = _contained_objects(og.sim.scene, container_obj)
        # Skip in case only a garbage object is inside the container.
        if len(self._cached_contained_objs) == 1:
            contained_obj = self._cached_contained_objs[0]
            if (contained_obj.category == self.obj_attrs.category
                    and contained_obj.name.startswith(self.obj_attrs.name)):
                return False
        return bool(self._cached_contained_objs)

    def transition(self, container_obj):
        t_results = TransitionResults()

        # Create a single garbage object to be added.
        all_pos, all_orn = [], []
        for contained_obj in self._cached_contained_objs:
            pos, orn = contained_obj.get_position_orientation()
            all_pos.append(pos)
            all_orn.append(orn)

        category = self.obj_attrs.category
        model = self.obj_attrs.model
        name = f"{self.obj_attrs.name}_{self._counter}"
        self._counter += 1
        scale = self.obj_attrs.scale

        model_root_path = f"{og_dataset_path}/objects/{category}/{model}"
        usd_path = f"{model_root_path}/usd/{model}.usd"

        garbage_obj = DatasetObject(
            prim_path=f"/World/{name}",
            usd_path=usd_path,
            category=category,
            name=f"{name}",
            scale=scale)

        garbage_obj_attrs = ObjectAttrs(
            obj=garbage_obj, pos=np.mean(all_pos, axis=0), orn=np.mean(all_orn, axis=0))
        t_results.add.append(garbage_obj_attrs)

        # Remove all contained objects.
        for contained_obj in self._cached_contained_objs:
            t_results.remove.append(contained_obj)

        # Turn off the container after the transition and reset things.
        if ToggledOn in container_obj.states:
            container_obj.states[ToggledOn].set_value(False)
        self._cached_contained_objs = None
        return t_results


class BlenderRule(BaseTransitionRule):
    def __init__(self, output_fluid, fluid_requirements=None, obj_requirements=None):
        """
        Transition rule to apply when objects are blended together

        Args:
            output_fluid (FluidSystem): Fluid to generate once all the input ingredients are blended
            fluid_requirements (None or dict): If specified, should map fluid system to the minimum number of fluid
                particles required in order to successfully blend
            obj_requirements (None or dict): If specified, should map object category names to minimum number of that
                type of object rqeuired in order to successfully blend
        """
        # We want to filter for any object that is included in @obj_requirements and also separately for blender
        individual_filters = {"blender": CategoryFilter("blender")}
        group_filters = {category: CategoryFilter(category) for category in obj_requirements.keys()}

        # Store internal variables
        self.output_fluid = output_fluid
        self.fluid_requirements = fluid_requirements
        self.obj_requirements = obj_requirements

        # Store a cached dictionary to check blender volumes so we don't have to do this later
        self._check_in_volume = OrderedDict()

        # Call super method
        super().__init__(individual_filters=individual_filters, group_filters=group_filters)

    def condition(self, individual_objects, group_objects):
        # TODO: Check blender if both toggled on and lid is closed!

        blender = individual_objects["blender"]
        # If this blender doesn't exist in our volume checker, we add it
        if blender.name not in self._check_in_volume:
            self._check_in_volume[blender.name] = lambda pos: blender.states[Filled].check_in_volume(pos.reshape(-1, 3))

        # Check to see which objects are inside the blender container
        for obj_category, objs in group_objects.items():
            inside_objs = []
            for obj in objs:
                if obj.states[Inside].get_value(blender):
                    inside_objs.append(obj)
            # Make sure the number of objects inside meets the required threshold, else we trigger a failure
            if len(inside_objs) < self.obj_requirements[obj_category]:
                return False
            # We mutate the group_objects in place so that we only keep the ones inside the blender
            group_objects[obj_category] = inside_objs

        # Check whether we have sufficient fluids as well
        for system, n_min_particles in self.fluid_requirements.items():
            if len(system.particle_instancers) > 0:
                particle_positions = np.concatenate([inst.particle_positions for inst in system.particle_instancers.values()], axis=0)
                n_particles_in_volume = np.sum(self._check_in_volume[blender.name](particle_positions))
                if n_particles_in_volume < n_min_particles:
                    return False
            else:
                # Fluid doesn't even exist yet, so we know the condition is not met
                return False

        # Our condition is whether we have sufficient ingredients or not
        return True

    def transition(self, individual_objects, group_objects):
        t_results = TransitionResults()
        blender = individual_objects["blender"]
        # For every object in group_objects, we remove them from the simulator
        for i, (obj_category, objs) in enumerate(group_objects.items()):
            for j, obj in enumerate(objs):
                t_results.remove.append(obj)

        # Hide all fluid particles that are inside the blender
        for system in self.fluid_requirements.keys():
            # No need to check for whether particle instancers exist because they must due to @self.condition passing!
            for inst in system.particle_instancers.values():
                indices = self._check_in_volume[blender.name](inst.particle_positions).nonzero()[0]
                current_visibilities = inst.particle_visibilities
                current_visibilities[indices] = 0
                inst.particle_visibilities = current_visibilities

        # Spawn in blended fluid!
        blender.states[Filled].set_value(self.output_fluid, True)

        return t_results


"""See the following example for writing simple rules.

  GenericTransitionRule(
      filters=(
          AndFilter(CategoryFilter("light"), StateFilter(ToggledOn, True)),
          CategoryFilter("key"),
      ),
      condition_fn=lambda light, key: dist(light, key) < 1,
      transition_fn=lambda light, key: light.states[ToggledOn].set_value(True),
  )
"""
# TODO: To prevent bugs for now, we make this READ-ONLY (tuple). In the future, rules should be allowed to be added
# dynamically
DEFAULT_RULES = (
    SlicingRule(),
    # Strawberry smoothie
    BlenderRule(
        output_fluid=StrawberrySmoothieSystem,
        fluid_requirements={MilkSystem: 10},
        obj_requirements={"strawberry": 5, "ice_cube": 5},
    ),
)