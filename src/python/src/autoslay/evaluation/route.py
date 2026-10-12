"""Bounded paths through the visible map. Risk weights are explicit heuristics."""
VERSION='1.0.0'

def evaluate_routes(map_view,context,max_nodes=500):
    nodes={n['node_id']:n for n in map_view.get('nodes',[])}
    stack=[([n['node_id']],0) for n in nodes.values() if n.get('available')]
    candidates=[]; expanded=0
    while stack and expanded<max_nodes:
        path,score=stack.pop(); expanded+=1
        node=nodes[path[-1]]; kind=node.get('type')
        values={'RestSite':5*context.urgency,'Shop':min(context.gold/40,6),
                'Elite':4-4*context.urgency,'Monster':1-context.urgency,
                'Unknown':0,'Treasure':4,'Boss':0,'Ancient':1}
        score+=values.get(kind,0)
        children=[x for x in node.get('children',[]) if x in nodes and x not in path]
        if not children: candidates.append(dict(path=path,utility=score,first_action_id=nodes[path[0]].get('action_id'),room_types=[nodes[x]['type'] for x in path]))
        else:
            for child in children: stack.append((path+[child],score))
    candidates.sort(key=lambda c:c['utility'],reverse=True)
    return dict(module_version=VERSION,candidates=candidates[:5],nodes=expanded,search_truncated=bool(stack),
                execution_authorized=False,assumptions=['visible room types; unknown events/monsters/rewards not inspected',
                    'heuristic path value; future HP/gold is not simulated'])
