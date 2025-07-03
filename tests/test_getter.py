
from aworld.core.context.base import Context
from aworld.core.context.prompts.dynamic_variables import create_context_field_getter, create_simple_field_getter, get_trajectories_from_context


context = Context()
context.trajectories.update({"steps":[1, 2, 3]})

getter = create_simple_field_getter("trajectories", context)
print(getter(context = context))

g = get_trajectories_from_context

