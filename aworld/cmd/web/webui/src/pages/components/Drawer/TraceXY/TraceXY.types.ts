export interface NodeData {
  show_name: string;
  event_id?: string;
  summary?: string;
  children?: NodeData[];
}

export interface FlowElements {
  nodes: any[];
  edges: any[];
}

export interface TraceXYProps {
  traceId?: string;
  traceQuery?: string;
  drawerVisible?: boolean;
}
