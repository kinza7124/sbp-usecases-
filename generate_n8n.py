import json
import re

def parse_markdown_to_n8n(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    workflows = []
    current_workflow = None
    current_node = None
    
    for line in lines:
        if line.startswith('# ') and 'USE CASE' in line:
            if current_workflow:
                workflows.append(current_workflow)
            
            # Extract use case number and name
            match = re.search(r'USE CASE (\d+) — (.*)', line)
            if match:
                uc_num = match.group(1)
                wf_name = match.group(2).strip()
                current_workflow = {
                    'name': f"UC{uc_num} - {wf_name}",
                    'nodes': [],
                    'connections': {}
                }
        elif current_workflow and line.startswith('### 1. Workflow Name'):
            pass # Skip, handled above
        elif current_workflow and line.startswith('#### Node '):
            if current_node:
                current_workflow['nodes'].append(current_node)
            
            match = re.search(r'#### Node \d+: (.*)', line)
            if match:
                node_name = match.group(1).strip()
                current_node = {
                    'parameters': {},
                    'name': node_name,
                    'type': 'n8n-nodes-base.unknown',
                    'typeVersion': 1,
                    'position': [250, 300]
                }
        elif current_node and line.startswith('- **Node Type:**'):
            node_type_str = line.split(':**')[1].strip()
            
            type_mapping = {
                'Webhook': 'n8n-nodes-base.webhook',
                'Postgres': 'n8n-nodes-base.postgres',
                'HTTP Request': 'n8n-nodes-base.httpRequest',
                'IF': 'n8n-nodes-base.if',
                'Manual Approval': 'n8n-nodes-base.manualTrigger',
                'Set': 'n8n-nodes-base.set',
                'Wait': 'n8n-nodes-base.wait',
                'OpenAI Chat': 'n8n-nodes-base.openAi',
                'Email': 'n8n-nodes-base.emailSend',
                'Schedule Trigger': 'n8n-nodes-base.scheduleTrigger',
                'Loop Over Items': 'n8n-nodes-base.splitInBatches',
                'Merge': 'n8n-nodes-base.merge',
                'Switch': 'n8n-nodes-base.switch',
            }
            
            # Find matching type
            for k, v in type_mapping.items():
                if k in node_type_str:
                    current_node['type'] = v
                    break
                    
        elif current_node and line.startswith('- **Configuration:**'):
            current_node['notes'] = line.split(':**')[1].strip()
            
    if current_node and current_workflow:
        current_workflow['nodes'].append(current_node)
    
    if current_workflow:
        workflows.append(current_workflow)
        
    return workflows

workflows = parse_markdown_to_n8n('n8n_aml_workflows_uc4_7_10_11_14.md')

# Update positions and connections
for wf in workflows:
    x, y = 250, 300
    prev_node = None
    for i, node in enumerate(wf['nodes']):
        node['position'] = [x, y]
        x += 200
        if i % 4 == 3:
            y += 200
            x = 250
            
        if prev_node:
            if prev_node['name'] not in wf['connections']:
                wf['connections'][prev_node['name']] = {"main": [[]]}
            wf['connections'][prev_node['name']]["main"][0].append({
                "node": node['name'],
                "type": "main",
                "index": 0
            })
        prev_node = node

    # Save to file
    filename = wf['name'].split(' - ')[0].lower() + '_workflow.json'
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(wf, f, indent=2)
        print(f"Generated {filename}")
