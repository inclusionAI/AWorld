/**
 * Node operation
 * adding、deleting
 */
import type { Node } from '@xyflow/react';
import { Position } from '@xyflow/react';

/**
 * addNode
 * @param nodes 
 * @param setNodes
 */
export const addNode = (
  nodes: Node[],
  setNodes: (nodes: Node[]) => void
): void => {
  const randomOffset = () => Math.random() * 50 - 25;

  const startNode = nodes.find((node) => node.type === 'input');
  const endNode = nodes.find((node) => node.type === 'output');

  if (!startNode || !endNode) {
    console.error('Start node or end node not found, unable to add a new node');
    return;
  }

  const newXPosition = endNode.position.x - randomOffset();
  const newYPosition = startNode.position.y + 150 + randomOffset();

  const newNode = {
    id: Date.now().toString(),
    type: 'customNode',
    data: { 
      label: `Custom Node ${nodes.length + 1}`,
      content: `This is custom node #${nodes.length + 1}`
    },
    position: {
      x: newXPosition,
      y: newYPosition
    },
    sourcePosition: Position.Right,
    targetPosition: Position.Left
  };

  setNodes([
    ...nodes.filter((node: Node) => node.id !== endNode.id),
    newNode,
    endNode
  ]);
};
