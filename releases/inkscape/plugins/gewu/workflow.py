"""Figure provenance, layout checks, and downstream artifact handoff."""
from document import atomic
from native import Failure
import svg


def sources(root,objects):
    result=[]
    for item in objects:
        if 'source' not in item: continue
        try:
            path=svg.inside(root,item['source'])
            current=svg.digest(svg.read_file(path)) if path.is_file() else None
            status=('unchanged' if current==item['sourceHash'] else 'changed') if current else 'missing'
        except (OSError,Failure):
            current=None; status='unavailable'
        result.append({'id':item['id'],'path':item['source'],'importedHash':item['sourceHash'],'currentHash':current,'status':status})
    return result


def check_layout(state,items):
    if not state['exists']: return []
    pages=state['pages']; issues=[]; panels=[]
    labels=[item['bbox'] for item in items if item['kind']=='label' and item['bbox']]
    for item in items:
        b=item['bbox']; key=item['id']
        if not b:
            issues.append({'code':'BOUNDS_UNAVAILABLE','ids':[key]}); continue
        x,y,w,h=(b[k] for k in ('x','y','width','height'))
        if not any(x>=p['x']-.05 and y>=p['y']-.05 and x+w<=p['x']+p['width']+.05 and y+h<=p['y']+p['height']+.05 for p in pages):
            issues.append({'code':'OFF_PAGE','ids':[key]})
        if item.get('rasterWidth') and w>0 and (dpi:=item['rasterWidth']*25.4/w)<300:
            issues.append({'code':'LOW_DPI','ids':[key],'dpi':round(dpi)})
        if item['kind']=='panel':
            panels.append((key,x,y,w,h))
            if not any(x-5<=b['x']<=x+max(5,w*.3) and y-8<=b['y']<=y+min(10,h*.3) for b in labels):
                issues.append({'code':'NO_NEARBY_LABEL','ids':[key],'heuristic':True})
    panels.sort(key=lambda p:p[1]); active=[]
    for key,x,y,w,h in panels:
        active=[p for p in active if p[1]+p[3]>x+.05]
        for other,ox,oy,ow,oh in active:
            if min(x+w,ox+ow)-x>.05 and min(y+h,oy+oh)-max(y,oy)>.05:
                issues.append({'code':'PANEL_OVERLAP','ids':[other,key]})
        active.append((key,x,y,w,h))
    return issues


def inner(root,panel):
    node=dict(svg.candidates(root)).get(panel)
    if node is None or node.get(svg.M('role'))!='panel':
        raise Failure('OBJECT_NOT_FOUND','No imported panel with that ID')
    return [{'id':source_id,'svgId':n.get('id'),'kind':'text','text':(n.text or '')[:2048]} for n in node.iter(svg.Q('text'))
            if not len(n) and (source_id:=n.get(svg.M('sourceId')))]


def handoff(consumer,path):
    instruction=('Use this immutable asset in a Paper manuscript figure block; Paper retains caption and citation ownership.'
                 if consumer=='paper' else 'Use this immutable PNG as a Manimble slide image; retain its figure ID and revision for later replacement.')
    return {'consumer':consumer,'workspacePath':path,'instructions':instruction}


def publish(workspace, project_path, consumer, figure_id, revision, artifact):
    project=svg.inside(workspace,project_path)
    if not project.is_dir():
        raise Failure('INVALID_PATH','The receiving project directory must already exist')
    folder=svg.inside(workspace,'figures' if consumer=='paper' else 'assets',project)
    if not folder.is_relative_to(project):
        raise Failure('INVALID_PATH','The asset directory must stay inside the receiving project')
    folder.mkdir(exist_ok=True)
    data=artifact.read_bytes()
    version=svg.digest(revision.encode()+data)[:12]; identity=svg.digest(figure_id.encode())[:12]
    out=folder/f'gewu-{identity}-{version}{artifact.suffix}'
    try: atomic(out,data,create=True)
    except FileExistsError:
        if out.is_symlink() or svg.read_file(out)!=data:
            raise Failure('EXTERNAL_MODIFICATION','Destination already exists with different bytes; it was not changed')
    return {'project':project.relative_to(workspace).as_posix(),'projectRelativePath':out.relative_to(project).as_posix(),
            'projectArtifact':out.relative_to(workspace).as_posix()}
