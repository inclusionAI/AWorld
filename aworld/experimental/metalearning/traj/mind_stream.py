"""
Mind Stream Module

This module provides trajectory data processing and parsing functionality, mainly for:
1. Reading and understanding trajectory data
2. Building and parsing task relationship graphs
3. Converting trajectory data to graph structures
4. Managing target agent configuration

Main functional modules:
- Trajectory understanding: Reading and processing trajectory data
- Graph structure parsing: Converting trajectory data to node and edge graph structures
- Agent configuration: Managing target agent ID settings
"""

from typing import Optional

from aworld.core.context.amni import ApplicationContext
from aworld.core.context.amni.services.trajectory_service import (
    TrajectoryService,
    save_context_artifact,
    TrajType,
)
from aworld.core.context.base import Context
from aworld.logs.util import logger


# coding: utf-8

####### Helper Functions #######

def set_generage_taget_agent_id(context: Context, target_agent_id: str) -> None:
    """Set the target agent ID in the context

    Store the target agent ID in the context for subsequent trajectory filtering.
    This ID is typically used to identify agents that require special handling or filtering.

    Args:
        context: Context object for storing configuration information
        target_agent_id: Target agent ID string
    """
    context.put('target_agent_id', target_agent_id)


def get_generate_target_agent_id(context: Context) -> Optional[str]:
    """Get the target agent ID from the context

    Read the previously set target agent ID from the context.

    Args:
        context: Context object containing configuration information

    Returns:
        Target agent ID string, or None if not set
    """
    return context.get('target_agent_id')


async def retrieve_traj_and_draw_mind_stream(context: Context):
    """Retrieve trajectory data and generate graph structure

    Get trajectory data from context, parse it into a graph structure, and save related artifacts.

    Args:
        context: Context object
    """
    if isinstance(context, ApplicationContext):
        context = context.root

    session_id = context.session_id
    if not session_id:
        logger.warning("MindStreamHook: session_id is None, skip artifact creation")
        return

    traj_data = await TrajectoryService.get_running_exp(context)
    if traj_data is None:
        logger.info("MindStreamHook: traj_data is None, skip graph generation")
        return

    graph_data = TrajectoryService.parse_traj_to_graph(context, traj_data)
    logger.info(f"graph_data: {graph_data}")

    # Runtime information
    meta_data = await TrajectoryService.get_running_meta(context, context.task_id)
    meta_artifact_id = await save_context_artifact(context, TrajType.META_DATA, meta_data)
    exp_artifact_id = await save_context_artifact(context, TrajType.EXP_DATA, TrajectoryService.convert_to_store_data(traj_data))
    graph_data_artifact_id = await save_context_artifact(context, TrajType.GRAPH_DATA, graph_data)

    logger.info(f"MindStreamHook: Successfully created graph structure artifact, exp_data_artifact_id={exp_artifact_id}, "
                f"graph_data_artifact_id={graph_data_artifact_id}, "
                f"meta_data_artifact_id={meta_artifact_id}, "
                f"nodes={len(graph_data.get('nodes', []))}, edges={len(graph_data.get('edges', []))}")
