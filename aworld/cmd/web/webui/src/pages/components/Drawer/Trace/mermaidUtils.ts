interface TraceNode {
  show_name: string;
  span_id?: string;
  duration_ms?: number;
  children?: TraceNode[];
}

/**
 * 将多层级children数据结构转换为mermaid流程图格式
 * @param data 输入数据，格式如：[{show_name:'a',children:[{show_name:'aa'}]}]
 * @returns mermaid流程图字符串
 */

export function treeToMermaid(input: any): string {
  let output = 'flowchart TD\n';
  const processedNodes = new Set<string>();

  function processNode(node: TraceNode, parentId?: string) {
    if (!node?.show_name) return;
    
    // 生成并清理节点ID
    const rawNodeId = `${node.show_name}_${node.span_id || ''}`.replace(/\s+/g, '_');
    const cleanNodeId = rawNodeId.replace(/[^a-zA-Z0-9_]/g, '_');
    
    // 添加节点定义（如果尚未处理过）
    if (!processedNodes.has(cleanNodeId)) {
      // 只保留字母、数字、空格和基本标点
      const cleanName = node.show_name
        .replace(/[^a-zA-Z0-9-\s\-_.,]/g, '')
        .trim();
      
      // 节点只显示名称
      output += `  ${cleanNodeId}["${cleanName}"]\n`;
      processedNodes.add(cleanNodeId);
    }

      // 添加与父节点的关系，显示duration
      if (parentId) {
        const cleanParentId = parentId.replace(/[^a-zA-Z0-9_]/g, '_');
        const duration = node.duration_ms ? `${node.duration_ms.toFixed(2)}ms` : '';
        output += `  ${cleanParentId} -->|${duration}| ${cleanNodeId}\n`;
    }

    // 递归处理子节点
    if (node.children && node.children.length > 0) {
      node.children.forEach((child: TraceNode) => processNode(child, cleanNodeId));
    }
  }

  // 处理输入数据
  if (!input) return output;

  // 创建根节点
  const rootNode: TraceNode = {
    show_name: 'Trace Root',
    span_id: 'root',
    children: [] as TraceNode[]
  };

  // 处理不同输入格式
  if (input.data && Array.isArray(input.data)) {
    // 处理带data属性的输入对象
    rootNode.children = input.data;
  } else if (Array.isArray(input)) {
    // 处理数组输入（向后兼容）
    rootNode.children = input;
  } else {
    // 处理单个节点输入（向后兼容）
    rootNode.children = [input];
  }

  // 处理根节点及其子节点
  processNode(rootNode);

  return output;
}
