"""Editable SVG panel model; geometry is expressed in millimetres."""
import base64
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re
from urllib.parse import unquote, urlsplit
from uuid import uuid4

import cssselect2
from lxml import etree as E
from PIL import Image
import tinycss2 as css

from native import Failure

SVG = 'http://www.w3.org/2000/svg'
INK = 'http://www.inkscape.org/namespaces/inkscape'
SOD = 'http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd'
XL = 'http://www.w3.org/1999/xlink'
G = 'https://gewu.dev/ns/figure-layout/1'
Q = lambda name: f'{{{SVG}}}{name}'
M = lambda name: f'{{{G}}}{name}'
LIMIT = 32 * 1024 * 1024
ID = re.compile(r'[A-Za-z_][A-Za-z0-9_.:-]{0,127}\Z')
NONART = {'defs','metadata','title','desc','namedview','style','symbol'}
UNSAFE = {'script','foreignObject','animate','animateMotion','animateTransform','set','discard','link','iframe'}


def digest(value):
    return hashlib.sha256(value).hexdigest()


def read_file(path):
    if not path.is_file() or path.stat().st_size > LIMIT:
        raise Failure('INVALID_PATH', 'Expected a regular workspace file of at most 32 MiB')
    with path.open('rb') as stream:
        result = stream.read(LIMIT+1)
    if len(result) > LIMIT:
        raise Failure('INVALID_PATH','Input file exceeds 32 MiB')
    return result


def inside(root, relative, base=None):
    p = Path(relative)
    if p.is_absolute() or '\\' in str(relative) or '\0' in str(relative):
        raise Failure('INVALID_PATH','Use a relative workspace path')
    candidate = ((base or root) / p).resolve()
    if not candidate.is_relative_to(root):
        raise Failure('INVALID_PATH','Path escapes the workspace')
    return candidate


def number(value, *, positive=False):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or abs(value)>100000 or (positive and value<=0):
        raise Failure('INVALID_GEOMETRY','Expected a finite coordinate or positive size in millimetres')
    return float(value)


def fmt(value):
    return format(value,'.12g')


def length(value):
    m = re.fullmatch(r'\s*([+-]?[\d.]+(?:[eE][+-]?\d+)?)\s*(px|mm|cm|in|pt|pc|q)?\s*',value or '')
    if m:
        try:
            return number(float(m[1]) * {None:25.4/96,'px':25.4/96,'mm':1,'cm':10,'in':25.4,'pt':25.4/72,'pc':25.4/6,'q':.25}[m[2]],positive=True)
        except ValueError:
            pass
    raise Failure('INVALID_GEOMETRY', 'SVG must have a finite physical width and height or viewBox')


def viewbox(root):
    raw = root.get('viewBox')
    if raw:
        try:
            x,y,w,h = (number(float(t)) for t in re.split(r'[\s,]+',raw.strip()))
            return x,y,number(w,positive=True),number(h,positive=True)
        except ValueError as e:
            raise Failure('INVALID_GEOMETRY','Invalid SVG viewBox') from e
    return 0,0,length(root.get('width'))*96/25.4,length(root.get('height'))*96/25.4


def page_frame(root):
    x,y,w,h = viewbox(root)
    width,height = (length(root.get(key)) if root.get(key) else fallback*25.4/96
                    for key,fallback in (('width',w),('height',h)))
    sx,sy = width/w,height/h
    policy = root.get('preserveAspectRatio','xMidYMid meet').split() or ['xMidYMid','meet']
    ax,ay = 0.,0.
    if policy[0] != 'none':
        scale = max(sx,sy) if policy[-1]=='slice' else min(sx,sy)
        ax = (width-scale*w) * (0 if 'xMin' in policy[0] else 1 if 'xMax' in policy[0] else .5)
        ay = (height-scale*h) * (0 if 'YMin' in policy[0] else 1 if 'YMax' in policy[0] else .5)
        sx=sy=scale
    return width,height,sx,sy,ax-x*sx,ay-y*sy


def parse(data):
    if len(data)>LIMIT:
        raise Failure('UNSUPPORTED_SVG','SVG is larger than 32 MiB')
    try:
        tree = E.fromstring(data, E.XMLParser(resolve_entities=False,no_network=True))
    except E.XMLSyntaxError as e:
        raise Failure('UNSUPPORTED_SVG',f'Invalid SVG: {str(e)[:200]}') from e
    if tree.tag != Q('svg') or tree.getroottree().docinfo.doctype or tree.getroottree().xpath('//processing-instruction()'):
        raise Failure('UNSUPPORTED_SVG','Expected a standalone SVG without a doctype or processing instruction')
    return tree


def encode(root):
    return E.tostring(root,xml_declaration=True,encoding='UTF-8')


def declarations(tokens):
    items = css.parse_declaration_list(tokens,skip_comments=True,skip_whitespace=True)
    if any(item.type != 'declaration' or item.name.startswith('--') for item in items):
        raise Failure('UNSUPPORTED_SVG','Only static CSS declarations are supported')
    return items


def refs(tokens, mapping):
    for token in tokens:
        if token.type == 'url' or (token.type == 'function' and token.lower_name == 'url'):
            value = token.value if token.type == 'url' else css.serialize(token.arguments).strip().strip('\'"')
            if not value.startswith('#') or value[1:] not in mapping:
                raise Failure('UNRESOLVED_ASSET',f'Unsupported or missing SVG fragment: {value[:120]}')
            value = '#'+mapping[value[1:]]
            if token.type == 'url':
                token.value = value
                token.representation = 'url("'+value+'")'
            else:
                token.arguments = css.parse_component_value_list('"'+value+'"')
        elif token.type == 'function':
            if token.lower_name in ('var','attr','env','image-set','src'):
                raise Failure('UNSUPPORTED_SVG','Dynamic CSS or external images are not supported')
            refs(token.arguments,mapping)
        elif token.type == 'error':
            raise Failure('UNSUPPORTED_SVG','Malformed CSS value')


def static_styles(root, materialize, mapping):
    matcher = cssselect2.Matcher()
    styles = list(root.iter(Q('style')))
    for node in styles:
        for rule in css.parse_stylesheet(node.text or '',skip_whitespace=True,skip_comments=True):
            selector = css.serialize(rule.prelude) if rule.type=='qualified-rule' else ''
            if not selector or re.search(r':(?:hover|active|focus[\w-]*|visited|target|link|checked|disabled|enabled)\b',selector,re.I):
                raise Failure('UNSUPPORTED_SVG','Unsupported dynamic CSS selector or at-rule')
            try:
                selectors = cssselect2.compile_selector_list(selector)
            except cssselect2.SelectorError as e:
                raise Failure('UNSUPPORTED_SVG','Unsupported SVG stylesheet selector') from e
            content = declarations(rule.content)
            for d in content:
                refs(d.value,mapping)
            for compiled in selectors:
                if compiled.pseudo_element:
                    raise Failure('UNSUPPORTED_SVG','CSS pseudo-elements are not supported')
                matcher.add_selector(compiled,content)
    for wrapper in cssselect2.ElementWrapper.from_xml_root(root).iter_subtree():
        node = wrapper.etree_element
        inline = declarations(node.get('style',''))
        winners = {}
        for specificity,order,_,rules in matcher.match(wrapper):
            for d in rules:
                rank = (d.important,0,specificity,order)
                if d.lower_name not in winners or winners[d.lower_name][0]<=rank:
                    winners[d.lower_name]=(rank,d)
        for d in inline:
            refs(d.value,mapping)
            rank = (d.important,1,(),0)
            if d.lower_name not in winners or winners[d.lower_name][0]<=rank:
                winners[d.lower_name]=(rank,d)
        if materialize and winners:
            node.set('style',';'.join(f'{d.lower_name}:{css.serialize(d.value).strip()}'+(' !important' if d.important else '')
                                      for _,d in winners.values()))
    if materialize:
        for node in styles:
            node.getparent().remove(node)


def image_uri(data):
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format not in ('PNG','JPEG') or image.width*image.height>60_000_000:
                raise ValueError('unsupported format/size')
            ext,width,height=image.format.lower(),image.width,image.height
            image.verify()
    except (OSError,ValueError,Image.DecompressionBombError) as e:
        raise Failure('UNRESOLVED_ASSET','Expected a valid PNG/JPEG under 60 megapixels') from e
    return f'data:image/{ext};base64,{base64.b64encode(data).decode()}',width,height


def validate(root, workspace, base, prefix=None, embed=False):
    mapping={}
    for node in root.iter():
        if not isinstance(node.tag,str):
            continue
        key=node.get('id')
        if key:
            if key in mapping:
                raise Failure('UNSUPPORTED_SVG',f'Duplicate SVG ID: {key[:80]}')
            mapping[key]=f'{prefix}-{len(mapping)}' if prefix else key
    static_styles(root,bool(prefix),mapping)
    for node in root.iter():
        if not isinstance(node.tag,str):
            continue
        local=E.QName(node).localname
        if local in UNSAFE:
            raise Failure('UNSUPPORTED_SVG',f'Unsupported SVG element: {local}')
        for attr,value in list(node.attrib.items()):
            name=E.QName(attr).localname
            if name.lower().startswith('on') or attr=='{http://www.w3.org/XML/1998/namespace}base':
                raise Failure('UNSUPPORTED_SVG','Executable attributes and xml:base are unsupported')
            if name in ('href','src'):
                if value.startswith('#') and value[1:] in mapping:
                    node.set(attr,'#'+mapping[value[1:]])
                elif local in ('image','feImage'):
                    if re.match(r'data:image/(?:png|jpeg);base64,',value):
                        if embed:
                            try:
                                image_uri(base64.b64decode(value.split(',',1)[1],validate=True))
                            except ValueError as e:
                                raise Failure('UNRESOLVED_ASSET','Invalid embedded image') from e
                    elif embed and not any((urlsplit(value).scheme,urlsplit(value).netloc,urlsplit(value).query,urlsplit(value).fragment)):
                        node.set(attr,image_uri(read_file(inside(workspace,unquote(value),base)))[0])
                    else:
                        raise Failure('UNRESOLVED_ASSET','Image must be embedded or an importable workspace-local PNG/JPEG')
                elif local=='a' and urlsplit(value).scheme in ('https','http','mailto'):
                    continue
                else:
                    raise Failure('UNRESOLVED_ASSET',f'Unsupported SVG reference on {local}')
            elif name not in ('style','id') and 'url' in value.lower() and not attr.startswith('{'):
                tokens=css.parse_component_value_list(value)
                refs(tokens,mapping)
                if prefix:
                    node.set(attr,css.serialize(tokens))
        if prefix and node.get('id'):
            if node.tag==Q('text') and not len(node): node.set(M('sourceId'),node.get('id'))
            node.set('id',mapping[node.get('id')])
    return root


def new_page(width,height):
    root=E.Element(Q('svg'),nsmap={None:SVG,'gewu':G,'inkscape':INK,'sodipodi':SOD,'xlink':XL})
    root.set(M('figure'),uuid4().hex)
    set_page(root,width,height)
    return root


def set_page(root,width,height,margin=None):
    width,height=number(width,positive=True),number(height,positive=True)
    if max(width,height)>2000:
        raise Failure('INVALID_GEOMETRY','Page sides are limited to 2000 mm')
    if root.get('viewBox'):
        _,_,sx,sy,tx,ty=page_frame(root)
        if abs(sx-sy)>1e-8:
            root.set('preserveAspectRatio','none')
        root.set('viewBox',' '.join(map(fmt,(-tx/sx,-ty/sy,width/sx,height/sy))))
    else:
        root.set('viewBox',f'0 0 {fmt(width)} {fmt(height)}')
    root.set('width',fmt(width)+'mm'); root.set('height',fmt(height)+'mm')
    if margin is not None:
        margin=number(margin)
        if not 0<=margin<min(width,height)/2:
            raise Failure('INVALID_GEOMETRY','Margin leaves no usable page area')
        root.set(M('margin'),fmt(margin))


def namedview(root):
    return root.find(f'{{{SOD}}}namedview')


def page_nodes(root):
    view=namedview(root)
    return [] if view is None else view.findall(f'{{{INK}}}page')


def pages(root):
    width,height,sx,sy,tx,ty=page_frame(root)
    explicit=page_nodes(root)
    if not explicit: return [{'number':1,'label':'Page 1','x':0,'y':0,'width':width,'height':height}]
    return [{'number':i,'label':p.get(f'{{{INK}}}label',f'Page {i}'),
             'x':number(float(p.get('x','0')))*sx+tx,'y':number(float(p.get('y','0')))*sy+ty,
             'width':number(float(p.get('width')),positive=True)*sx,'height':number(float(p.get('height')),positive=True)*sy}
            for i,p in enumerate(explicit,1)]


def page_copy(root,page):
    if not page_nodes(root): return root
    p=pages(root)[page-1]; _,_,sx,sy,tx,ty=page_frame(root)
    clone=deepcopy(root); view=namedview(clone)
    for node in page_nodes(clone): view.remove(node)
    clone.set('width',fmt(p['width'])+'mm'); clone.set('height',fmt(p['height'])+'mm')
    clone.set('viewBox',' '.join(map(fmt,((p['x']-tx)/sx,(p['y']-ty)/sy,p['width']/sx,p['height']/sy))))
    clone.set('preserveAspectRatio','none')
    return clone


def add_page(root,width,height,label):
    width,height=number(width,positive=True),number(height,positive=True)
    if max(width,height)>2000: raise Failure('INVALID_GEOMETRY','Page sides are limited to 2000 mm')
    _,_,sx,sy,tx,ty=page_frame(root)
    existing=pages(root); view=namedview(root)
    if len(existing)>=100: raise Failure('INVALID_GEOMETRY','Figure supports at most 100 pages')
    if view is None: view=E.SubElement(root,f'{{{SOD}}}namedview',id=allocated(root))
    def append(x,y,w,h,text):
        node=E.SubElement(view,f'{{{INK}}}page',id=allocated(root),x=fmt((x-tx)/sx),y=fmt((y-ty)/sy),width=fmt(w/sx),height=fmt(h/sy))
        node.set(f'{{{INK}}}label',text)
    if not page_nodes(root):
        p=existing[0]; append(p['x'],p['y'],p['width'],p['height'],p['label'])
    append(max(p['x']+p['width'] for p in existing)+10,0,width,height,label or f'Page {len(existing)+1}')


def meta(root,name):
    node=root.find(Q('metadata'))
    return json.loads(node.get(M(name),'{}')) if node is not None else {}


def save_meta(root,kind,name,value):
    saved=meta(root,kind)
    if value is None:
        if name not in saved: raise Failure('INVALID_ARGUMENT',f'Unknown {kind}: {name}')
        del saved[name]
    else:
        if name not in saved and len(saved)>=12: raise Failure('INVALID_ARGUMENT',f'At most 12 {kind} are supported')
        saved[name]=value
    node=root.find(Q('metadata'))
    if node is None:
        node=E.Element(Q('metadata')); root.insert(0,node)
    node.set(M(kind),json.dumps(saved,separators=(',',':')))


def style_text(root,node,op,inner=False):
    style={d.lower_name:css.serialize(d.value).strip()+(' !important' if d.important else '') for d in declarations(node.get('style',''))}
    if 'font' in op: style['font-family']=op['font']
    if 'size' in op: style['font-size']=fmt(number(op['size'],positive=True)/(1 if inner else page_frame(root)[3]))
    if 'color' in op: style['fill']=op['color']
    node.set('style',';'.join(f'{k}:{v}' for k,v in style.items()))


def panel(root, workspace, path, key, x,y,width):
    src=inside(workspace,path)
    raw=read_file(src)
    group=E.Element(Q('g'),id=key)
    group.set(M('role'),'panel'); group.set(M('source'),src.relative_to(workspace).as_posix()); group.set(M('sourceHash'),digest(raw))
    if src.suffix.lower()=='.svg':
        content=validate(parse(raw),workspace,src.parent,prefix=key+'-'+uuid4().hex[:8],embed=True)
        w,h,vx,vy,dx,dy=page_frame(content)
        bx,by,bw,bh=viewbox(content)
        viewport=E.SubElement(group,Q('g'))
        viewport.set('transform',f'matrix({fmt(vx)} 0 0 {fmt(vy)} {fmt(dx+bx*vx)} {fmt(dy+by*vy)})')
        content.set('viewBox',' '.join(map(fmt,(bx,by,bw,bh))))
        content.attrib.pop('x',None); content.attrib.pop('y',None)
        content.set('width',fmt(bw)); content.set('height',fmt(bh)); content.set('overflow','hidden')
        for child in list(content):
            if isinstance(child.tag,str) and E.QName(child).localname=='namedview':
                content.remove(child)
        viewport.append(content)
    elif src.suffix.lower()=='.png':
        uri,w,h=image_uri(raw)
        if not uri.startswith('data:image/png'):
            raise Failure('UNSUPPORTED_SVG','File is not PNG')
        E.SubElement(group,Q('image'),width=str(w),height=str(h)).set('href',uri)
        group.set(M('rasterWidth'),str(w))
    else:
        raise Failure('UNSUPPORTED_SVG','Panel source must be SVG or PNG')
    x,y,width=number(x),number(y),number(width,positive=True)
    _,_,sx,sy,tx,ty=page_frame(root)
    group.set('transform',f'translate({fmt((x-tx)/sx)} {fmt((y-ty)/sy)}) scale({fmt(width/w/sx)} {fmt(width/w/sy)})')
    return group


def candidates(root):
    return [(node.get('id') or f'@{i}',node) for i,node in enumerate(root)
            if isinstance(node.tag,str) and E.QName(node).namespace==SVG and E.QName(node).localname not in NONART]


def allocated(root, key=None):
    key=key or 'o-'+uuid4().hex
    if not ID.fullmatch(key) or any(n.get('id')==key for n in root.iter()):
        raise Failure('INVALID_ARGUMENT','Object ID is invalid or already exists')
    return key


def locked(node):
    return node.get(M('locked'))=='true' or node.get(f'{{{SOD}}}insensitive')=='true'


def translate(root,node,dx,dy):
    _,_,sx,sy,_,_=page_frame(root)
    node.set('transform',f'translate({fmt(number(dx)/sx)} {fmt(number(dy)/sy)}) '+node.get('transform',''))


def apply(root, op, workspace, measure):
    kind=op['op']
    ids=op.get('ids',[op['id']] if 'id' in op else [])
    by_id=dict(candidates(root))

    def pick(key, allow_locked=False):
        if key not in by_id:
            raise Failure('OBJECT_NOT_FOUND',f'No top-level object {key}')
        node=by_id[key]
        if locked(node) and not allow_locked:
            raise Failure('OBJECT_LOCKED',f'{key} is locked')
        return node

    if kind=='set_page':
        if page_nodes(root): raise Failure('INVALID_GEOMETRY','Single-page resizing is unavailable in a multipage figure')
        set_page(root,op['width'],op['height'],op.get('margin'))
        return []
    if kind=='add_page':
        add_page(root,op['width'],op['height'],op.get('label'))
        return []
    if kind=='rename_page':
        items=page_nodes(root)
        if not 1<=op['page']<=len(items): raise Failure('INVALID_GEOMETRY','Page is not a native named page')
        items[op['page']-1].set(f'{{{INK}}}label',op['label']); return []
    if kind=='save_rule':
        from jsonschema import Draft202012Validator
        from contract import OPERATIONS
        schemas={s['properties']['op']['const']:s for s in OPERATIONS}
        for rule in op['operations']:
            if rule.get('op') not in ('align','distribute','equalize') or not Draft202012Validator(schemas[rule['op']]).is_valid(rule):
                raise Failure('INVALID_ARGUMENT','A rule can only contain valid align, distribute and equalize operations')
        save_meta(root,'rules',op['name'],op['operations']); return []
    if kind=='remove_rule': save_meta(root,'rules',op['name'],None); return []
    if kind=='apply_rule':
        rules=meta(root,'rules').get(op['name'])
        if rules is None or len(rules)>30 or any(rule.get('op') not in ('align','distribute','equalize') for rule in rules):
            raise Failure('INVALID_ARGUMENT','Unknown or invalid layout rule')
        return [key for rule in rules for key in apply(root,rule,workspace,measure)]
    if kind=='set_preset':
        save_meta(root,'presets',op['name'],{k:v for k,v in op.items() if k not in ('op','name')}); return []
    if kind=='apply_preset':
        preset=meta(root,'presets').get(op['name'])
        if preset is None: raise Failure('INVALID_ARGUMENT','Unknown style preset')
        if 'margin' in preset:
            w,h,*_=page_frame(root); margin=number(preset['margin'])
            if not 0<=margin<min(w,h)/2: raise Failure('INVALID_GEOMETRY','Margins leave no usable page area')
            root.set(M('margin'),fmt(margin))
        for key in ids: apply(root,{'op':'set_text_style','id':key,**preset},workspace,measure)
        return ids
    if kind in ('set_inner_text','set_inner_text_style'):
        group=pick(op['panel'])
        found=[n for n in group.iter(Q('text')) if n.get(M('sourceId'))==op['id'] and not len(n)]
        if len(found)!=1: raise Failure('OBJECT_NOT_FOUND','Inspect this panel for editable text IDs')
        if kind=='set_inner_text': found[0].text=op['text']
        else: style_text(root,found[0],op,inner=True)
        return [op['panel']]
    if kind=='import_panel':
        if any(root.iter(Q('style'))):
            static_styles(root,True,{n.get('id'):n.get('id') for n in root.iter() if n.get('id')})
        key=allocated(root,op.get('id'))
        root.append(panel(root,workspace,op['path'],key,op.get('x',0),op.get('y',0),op.get('width',40)))
        return [key]
    if kind=='add_text':
        key=allocated(root,op.get('id'))
        _,_,sx,sy,tx,ty=page_frame(root)
        node=E.SubElement(root,Q('text'),id=key,x=fmt((number(op['x'])-tx)/sx),y=fmt((number(op['y'])-ty)/sy))
        node.set(M('role'),'label'); node.text=op['text']
        node.set('font-size',fmt(number(op.get('size',3.5),positive=True)/sy))
        node.set('font-family',op.get('font','DejaVu Sans'))
        if 'color' in op: node.set('fill',op['color'])
        return [key]
    if kind=='adopt_objects':
        result=[]
        for key in ids:
            node=pick(key)
            if node.get(M('role')):
                result.append(key); continue
            group=E.Element(Q('g'),id=allocated(root)); group.set(M('role'),'panel')
            root.replace(node,group); group.append(node); result.append(group.get('id'))
        return result
    nodes=[pick(key,kind=='set_locked') for key in ids]
    if not nodes or len(set(nodes))!=len(nodes):
        raise Failure('INVALID_ARGUMENT','Supply distinct object IDs')
    if kind=='move':
        for node in nodes: translate(root,node,op['dx'],op['dy'])
    elif kind in ('align','distribute','resize','replace_panel','equalize'):
        bounds=measure(root)
        def box(key):
            if key not in bounds:
                raise Failure('INVALID_GEOMETRY',f'Native bounds unavailable for {key}')
            return bounds[key]

        if kind=='align':
            ref=op['reference']
            if ref in ('page','margin'):
                width,height,*_=page_frame(root)
                margin=float(root.get(M('margin'),'0')) if ref=='margin' else 0
                baseline={'x':margin,'y':margin,'width':width-2*margin,'height':height-2*margin}
            else:
                pick(ref,True); baseline=box(ref)
            axis,dim,factor={'left':('x','width',0),'right':('x','width',1),'top':('y','height',0),
                             'bottom':('y','height',1),'center_x':('x','width',.5),'center_y':('y','height',.5)}[op['edge']]
            for key,node in zip(ids,nodes):
                b=box(key); delta=baseline[axis]+factor*baseline[dim]-b[axis]-factor*b[dim]
                translate(root,node,delta if axis=='x' else 0, delta if axis=='y' else 0)
        elif kind=='distribute':
            axis=op['axis']; dim='width' if axis=='x' else 'height'; gap=number(op['gap'])
            if gap<0: raise Failure('INVALID_GEOMETRY','Gap cannot be negative')
            cursor=box(ids[0])[axis]
            for key,node in zip(ids,nodes):
                b=box(key); delta=cursor-b[axis]
                translate(root,node,delta if axis=='x' else 0,delta if axis=='y' else 0)
                cursor+=b[dim]+gap
        elif kind in ('resize','equalize'):
            dim=op.get('dimension','width')
            target=number(op['size'] if 'size' in op else op['width'],positive=True) if kind=='resize' else box(op['reference'])[dim]
            _,_,sx,sy,tx,ty=page_frame(root)
            for key,node in zip(ids,nodes):
                b=box(key); factor=target/number(b[dim],positive=True)
                x=(b['x']-tx)/sx; y=(b['y']-ty)/sy
                node.set('transform',f'translate({fmt(x*(1-factor))} {fmt(y*(1-factor))}) scale({fmt(factor)}) '+node.get('transform',''))
        else:
            old=nodes[0]; b=box(ids[0])
            if old.get(M('role'))!='panel' or old.get(M('source')) is None:
                raise Failure('INVALID_ARGUMENT','Only imported panels can be replaced')
            new=panel(root,workspace,op.get('path',old.get(M('source'))),ids[0],b['x'],b['y'],b['width'])
            root.replace(old,new); actual=measure(root).get(ids[0]); _,_,sx,sy,tx,ty=page_frame(root)
            if not actual or min(actual['width'],actual['height'])<=0:
                raise Failure('INVALID_GEOMETRY','Replacement has no measurable bounds')
            factor=b['width']/actual['width']
            ax=(b['x']-tx-factor*(actual['x']-tx))/sx; ay=(b['y']-ty-factor*(actual['y']-ty))/sy
            new.set('transform',f'translate({fmt(ax)} {fmt(ay)}) scale({fmt(factor)}) '+new.get('transform',''))
    elif kind in ('set_text','set_text_style'):
        node=nodes[0]
        if node.get(M('role'))!='label' and (node.tag!=Q('text') or len(node)):
            raise Failure('INVALID_ARGUMENT','Only simple SVG text or plugin labels are directly editable')
        if kind=='set_text': node.text=op['text']
        else: style_text(root,node,op)
    elif kind=='set_locked':
        for node in nodes:
            node.set(M('locked'),str(op['locked']).lower())
            node.set(f'{{{SOD}}}insensitive',str(op['locked']).lower())
    elif kind=='delete':
        for node in nodes: root.remove(node)
    elif kind=='reorder':
        ordered=sorted(nodes,key=root.index)
        for node in ordered: root.remove(node)
        if op['position']=='front': root.extend(ordered)
        else:
            start=min((root.index(n) for _,n in candidates(root)),default=len(root))
            for i,node in enumerate(ordered,start): root.insert(i,node)
    elif kind=='group':
        ordered=sorted(nodes,key=root.index)
        indices=[i for i,(_,node) in enumerate(candidates(root)) if node in nodes]
        if indices!=list(range(indices[0],indices[0]+len(indices))):
            raise Failure('INVALID_ARGUMENT','Group a contiguous stacking range or reorder it first')
        group=E.Element(Q('g'),id=allocated(root,op.get('groupId'))); group.set(M('role'),'group')
        root.insert(root.index(ordered[0]),group); group.extend(ordered)
        return [group.get('id')]
    elif kind=='ungroup':
        result=[]
        for group in nodes:
            if group.get(M('role'))!='group':
                raise Failure('INVALID_ARGUMENT','Only managed groups can be ungrouped')
            if any(E.QName(key).localname not in ('id','role','transform','locked','insensitive') for key in group.attrib):
                raise Failure('INVALID_ARGUMENT','Ungrouping would change group-level appearance')
            for child in list(group):
                child.set('transform',(group.get('transform','')+' '+child.get('transform','')).strip())
                group.addprevious(child); result.append(child.get('id'))
            root.remove(group)
        return result
    else:
        raise Failure('INVALID_ARGUMENT',f'Unsupported edit operation: {kind}')
    return ids
