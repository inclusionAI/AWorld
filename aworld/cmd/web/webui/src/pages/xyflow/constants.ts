import { Position } from '@xyflow/react';

export const initialNodes = [
  {
    id: '1',
    type: 'input',
    data: { label: 'Start Node' },
    position: { x: 0, y: 0 },
    style: { background: '#E8F8F5', border: '2px solid #1ABC9C', color: '#16A085' },
    sourcePosition: Position.Right,
    targetPosition: Position.Left
  },
  {
    id: '2',
    type: 'output',
    data: { label: 'End Node' },
    position: { x: 400, y: 0 },
    style: { background: '#FEF9E7', border: '2px solid #F7DC6F', color: '#D4AC0D' },
    sourcePosition: Position.Right,
    targetPosition: Position.Left
  }
];
export const initialEdges = [];
