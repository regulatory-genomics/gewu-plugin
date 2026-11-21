"""Targeted edits of native SVG, preserving layers, definitions and nested objects."""
from copy import deepcopy
import json
import math
from pathlib import Path
import re
import tempfile
from uuid import uuid4
from urllib.parse import urlsplit,unquote
import base64
import mimetypes
import os

from jsonschema import Draft202012Validator
import numpy as np
from svgpathtools import parse_path, Path as SVGPath, Line, CubicBezier, QuadraticBezier, Arc
from svgpathtools.parser import parse_transform
import tinycss2

from native import Failure
import svg


ID={'type':'string','minLength':1,'maxLength':256}
NUM={'type':'number','minimum':-100000,'maximum':100000}
POS={'type':'number','exclusiveMinimum':0,'maximum':100000}
IDS={'type':'array','items':ID,'minItems':1,'maxItems':100,'uniqueItems':True}
BOOL={'type':'boolean'}
TEXT={'type':'string','maxLength':10000}
STYLE={'type':'object','minProperties':1,'maxProperties':30,'additionalProperties':{'type':'string','maxLength':256}}


def schema(fields,required):
    return {'type':'object','properties':fields,'required':required,'additionalProperties':False}


SPECS={
    'move':({'ids':IDS,'dx':NUM,'dy':NUM},['ids','dx','dy']),
    'resize':({'ids':IDS,'width':POS,'height':POS,'size':POS,'dimension':{'enum':['width','height']},'keepAspect':BOOL},['ids']),
    'rotate':({'ids':IDS,'angle':NUM,'x':NUM,'y':NUM},['ids','angle']),
    'flip':({'ids':IDS,'axis':{'enum':['x','y']}},['ids','axis']),
    'align':({'ids':IDS,'reference':ID,'edge':{'enum':['left','right','top','bottom','center_x','center_y']}},['ids','reference','edge']),
    'distribute':({'ids':IDS,'axis':{'enum':['x','y']},'gap':NUM},['ids','axis','gap']),
    'equalize':({'ids':IDS,'reference':ID,'dimension':{'enum':['width','height']}},['ids','reference','dimension']),
    'set_text':({'id':ID,'text':TEXT},['id','text']),
    'set_text_style':({'id':ID,'font':ID,'size':POS,'color':ID},['id']),
    'set_style':({'ids':IDS,'style':STYLE},['ids','style']),
    'add_text':({'id':ID,'text':TEXT,'x':NUM,'y':NUM,'font':ID,'size':POS,'color':ID},['text','x','y']),
    'create_rect':({'id':ID,'x':NUM,'y':NUM,'width':POS,'height':POS,'parent':ID,'style':STYLE},['x','y','width','height']),
    'create_ellipse':({'id':ID,'cx':NUM,'cy':NUM,'rx':POS,'ry':POS,'parent':ID,'style':STYLE},['cx','cy','rx','ry']),
    'create_path':({'id':ID,'d':TEXT,'parent':ID,'style':STYLE},['d']),
    'set_path':({'id':ID,'d':TEXT},['id','d']),
    'set_path_node':({'id':ID,'index':{'type':'integer','minimum':0},'x':NUM,'y':NUM},['id','index','x','y']),
    'group':({'ids':IDS,'groupId':ID},['ids']),
    'ungroup':({'ids':IDS},['ids']),
    'delete':({'ids':IDS},['ids']),
    'reorder':({'ids':IDS,'position':{'enum':['front','back','raise','lower']}},['ids','position']),
    'set_locked':({'ids':IDS,'locked':BOOL},['ids','locked']),
    'set_visible':({'ids':IDS,'visible':BOOL},['ids','visible']),
    'create_layer':({'id':ID,'label':ID,'parent':ID},['label']),
    'rename_layer':({'id':ID,'label':ID},['id','label']),
    'move_to_layer':({'ids':IDS,'layer':ID},['ids','layer']),
    'set_clip':({'ids':IDS,'source':ID},['ids','source']),
    'release_clip':({'ids':IDS},['ids']),
    'set_mask':({'ids':IDS,'source':ID},['ids','source']),
    'release_mask':({'ids':IDS},['ids']),
    'boolean':({'ids':IDS,'operation':{'enum':['union','difference','intersection','object_to_path']}},['ids','operation']),
    'import_panel':({'path':ID,'id':ID,'x':NUM,'y':NUM,'width':POS,'page':{'type':'integer','minimum':1},'text':{'enum':['preserve','paths']}},['path']),
    'set_page':({'width':POS,'height':POS,'margin':NUM},['width','height']),
    'add_page':({'width':POS,'height':POS,'label':ID},['width','height']),
    'rename_page':({'page':{'type':'integer','minimum':1},'label':ID},['page','label']),
    'add_guide':({'x':NUM,'y':NUM,'orientation':{'enum':['horizontal','vertical']}},['x','y','orientation']),
}
SCHEMAS={name:schema({'op':{'const':name},**fields},['op',*required]) for name,(fields,required) in SPECS.items()}
VALIDATORS={name:Draft202012Validator(value) for name,value in SCHEMAS.items()}
DRAWABLE={'g','svg','rect','circle','ellipse','path','text','tspan','image','use','line','polyline','polygon','flowRoot','flowPara'}
STYLE_PROPERTIES={'fill','fill-opacity','fill-rule','stroke','stroke-width','stroke-opacity','stroke-linecap','stroke-linejoin','stroke-dasharray','stroke-dashoffset','opacity','font-family','font-size','font-style','font-weight','text-anchor','display','visibility','clip-rule'}


class NativeDocument:
    def __init__(self,raw,workspace,engine=None,base=None):
        self.root=svg.parse(raw);self.workspace=Path(workspace);self.engine=engine;self.boxes=None;self.base=Path(base) if base else self.workspace
        seen=set()
        for node in self.root.iter():
            if not isinstance(node.tag,str):continue
            local=svg.E.QName(node).localname
            if local in ('script','foreignObject') or any(svg.E.QName(key).localname.lower().startswith('on') for key in node.attrib):
                raise Failure('UNSUPPORTED_SVG','Executable SVG content is not supported')
            key=node.get('id')
            if key and key in seen: raise Failure('UNSUPPORTED_SVG','Duplicate native object ID')
            if key:seen.add(key)

    @staticmethod
    def relocate(raw,source,target,workspace,*,embed=False):
        root=svg.parse(raw);source=Path(source);target=Path(target);workspace=Path(workspace).resolve()
        for node in root.iter():
            if not isinstance(node.tag,str):continue
            if node.get('{http://www.w3.org/XML/1998/namespace}base'):raise Failure('UNSUPPORTED_SVG','xml:base assets must be imported explicitly')
            for key,value in list(node.attrib.items()):
                if svg.E.QName(key).localname!='href' or value.startswith(('#','data:')):continue
                parsed=urlsplit(value)
                if parsed.scheme not in ('','file') or parsed.netloc:raise Failure('UNRESOLVED_ASSET','Native assets must be local workspace files or embedded data')
                path=(source/unquote(parsed.path)).resolve()
                if not path.is_relative_to(workspace) or not path.is_file():raise Failure('UNRESOLVED_ASSET','Linked native asset is missing or outside the workspace')
                if embed and svg.E.QName(node).localname in ('image','feImage'):
                    data=svg.read_file(path);mime=mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
                    node.set(key,'data:'+mime+';base64,'+base64.b64encode(data).decode())
                else:node.set(key,os.path.relpath(path,target)+('#'+parsed.fragment if parsed.fragment else ''))
                node.attrib.pop('{'+svg.SOD+'}absref',None)
        return svg.encode(root)

    def encode(self):return svg.encode(self.root)

    def nodes(self):
        return [node for node in self.root.iter() if isinstance(node.tag,str) and svg.E.QName(node).namespace==svg.SVG and svg.E.QName(node).localname in DRAWABLE and node is not self.root]

    def ref(self,node):return node.get('id') or '@'+self.root.getroottree().getpath(node)

    def pick(self,key,write=False):
        found=[node for node in self.root.iter() if isinstance(node.tag,str) and (node.get('id')==key or (not node.get('id') and self.ref(node)==key))]
        if len(found)!=1:raise Failure('OBJECT_NOT_FOUND',f'No unique native object {key}')
        node=found[0]
        if node is self.root:raise Failure('UNSUPPORTED_OBJECT','Use page operations for the SVG root')
        if write and any(svg.locked(n) for n in [node,*node.iterancestors()]):raise Failure('OBJECT_LOCKED',f'{key} is locked')
        return node

    def inspect(self,ids=None,parent=None,kind=None,text=None,offset=0,limit=50,fields=None):
        if not 1<=limit<=50 or offset<0:raise Failure('INVALID_ARGUMENT','Use limit 1–50 and a nonnegative offset')
        nodes=[self.pick(key) for key in ids] if ids else self.nodes()
        if parent is not None:nodes=[n for n in nodes if self.ref(n.getparent())==parent]
        if kind:nodes=[n for n in nodes if svg.E.QName(n).localname==kind]
        if text:nodes=[n for n in nodes if text in ''.join(n.itertext())]
        out=[];cost=0
        for n in nodes[offset:offset+limit]:
            attrs=n.attrib;item={'id':self.ref(n),'kind':'layer' if attrs.get('{'+svg.INK+'}groupmode')=='layer' else svg.E.QName(n).localname,
                'parent':self.ref(n.getparent()) if n.getparent() is not self.root else None,
                'label':attrs.get('{'+svg.INK+'}label'),'locked':any(svg.locked(x) for x in [n,*n.iterancestors()]),
                'text':''.join(n.itertext())[:2048] if svg.E.QName(n).localname in ('text','tspan','flowPara') else None}
            if fields and 'style' in fields:item['style']=dict(self.style(n))
            if fields and 'path' in fields and n.tag==svg.Q('path'):
                d=n.get('d','')
                if len(d)>5000:raise Failure('RESULT_TOO_LARGE','Path is large; request its file resource instead of inline geometry')
                item['path']=d
            if fields and 'bbox' in fields:item['bbox']=self.measure().get(n.get('id'))
            size=len(json.dumps(item).encode())
            if cost+size>7500:
                if not out:raise Failure('RESULT_TOO_LARGE','Use narrower inspect fields')
                break
            out.append(item);cost+=size
        end=offset+len(out)
        return {'objects':out,'total':len(nodes),'nextOffset':end if end<len(nodes) else None}

    def measure(self):
        if self.boxes is None:
            if self.engine is None:raise Failure('DEPENDENCY_MISSING','Native bounds require the Inkscape engine')
            with tempfile.TemporaryDirectory() as temp:
                p=Path(temp)/'input.svg';p.write_bytes(self.relocate(self.encode(),self.base,p.parent,self.workspace));self.boxes=self.engine.query(p)
        return self.boxes

    @staticmethod
    def style(node):
        return {d.name:tinycss2.serialize(d.value).strip() for d in tinycss2.parse_declaration_list(node.get('style',''),skip_comments=True,skip_whitespace=True) if d.type=='declaration'}

    def set_style(self,node,updates):
        if set(updates)-STYLE_PROPERTIES or any(re.search(r'url\(|[;{}]|javascript:|var\(',v,re.I) for v in updates.values()):
            raise Failure('INVALID_ARGUMENT','Use static supported style properties; referenced definitions are preserved separately')
        updates=updates.copy()
        for key in ('font-size','stroke-width'):
            if key in updates and re.fullmatch(r'[0-9.]+mm',updates[key]):
                scale=float(np.linalg.norm(self.matrix(node)[:2,1]))
                if scale==0:raise Failure('INVALID_GEOMETRY','Cannot style an object with a singular transform')
                updates[key]=svg.fmt(float(updates[key][:-2])/scale)
        current=self.style(node);current.update(updates);node.set('style',';'.join(f'{k}:{v}' for k,v in current.items()))

    def matrix(self,node):
        if node is self.root:
            _,_,sx,sy,tx,ty=svg.page_frame(self.root);return np.array([[sx,0,tx],[0,sy,ty],[0,0,1.]])
        return self.matrix(node.getparent())@parse_transform(node.get('transform',''))

    def transform(self,node,world):
        parent=self.matrix(node.getparent())
        try:local=np.linalg.inv(parent)@world@parent@parse_transform(node.get('transform',''))
        except np.linalg.LinAlgError as error:raise Failure('INVALID_GEOMETRY','Object has a singular parent transform') from error
        if not np.isfinite(local).all():raise Failure('INVALID_GEOMETRY','Transform is not finite')
        node.set('transform','matrix('+','.join(svg.fmt(float(x)) for x in [local[0,0],local[1,0],local[0,1],local[1,1],local[0,2],local[1,2]])+')')

    def new_id(self,key=None):return svg.allocated(self.root,key or 'o-'+uuid4().hex)

    def apply(self,operations):
        if not 1<=len(operations)<=50:raise Failure('INVALID_ARGUMENT','Provide 1–50 native operations')
        original=deepcopy(self.root);affected=[]
        try:
            for op in operations:
                validator=VALIDATORS.get(op.get('op'))
                error=next(validator.iter_errors(op),None) if validator else None
                if not validator or error:raise Failure('INVALID_ARGUMENT',f'Invalid {op.get("op")} arguments: {error.message if error else "query capabilities for available operations"}')
                affected.extend(self._apply(op));self.boxes=None
        except BaseException:
            self.root=original;self.boxes=None;raise
        return list(dict.fromkeys(affected))

    def _apply(self,op):
        action=op['op'];ids=op.get('ids',[op['id']] if 'id' in op else [])
        nodes=[self.pick(key,write=action not in ('set_locked','set_visible')) for key in ids] if action not in ('add_text','create_rect','create_ellipse','create_path','create_layer','import_panel') else []
        if any(a is not b and a in b.iterancestors() for a in nodes for b in nodes):raise Failure('INVALID_ARGUMENT','Select a group or its descendants, not both')
        if action in ('move','resize','rotate','flip','align','distribute','equalize'):
            bounds=self.measure() if action!='move' else {}
            def bbox(node):
                b=bounds.get(node.get('id'))
                if not b:raise Failure('UNSUPPORTED_OBJECT','Object has no measurable native bounds')
                return b
            if action=='distribute':nodes.sort(key=lambda n:bbox(n)[op['axis']])
            cursor=None
            for n in nodes:
                change=np.eye(3)
                if action=='move':change[0,2]=op['dx'];change[1,2]=op['dy']
                elif action in ('resize','equalize'):
                    b=bbox(n);w=b['width'];h=b['height']
                    if w<=0 or h<=0:raise Failure('INVALID_GEOMETRY','Cannot scale an empty object')
                    width=op.get('width');height=op.get('height')
                    if 'size' in op:
                        if op.get('dimension','width')=='width':width=op['size']
                        else:height=op['size']
                    if action=='equalize':
                        ref=bbox(self.pick(op['reference']));width=ref['width'] if op['dimension']=='width' else None;height=ref['height'] if op['dimension']=='height' else None
                    if width is None and height is None:raise Failure('INVALID_ARGUMENT','Resize requires width, height or size')
                    sx=width/w if width else height/h;sy=height/h if height else width/w
                    if not op.get('keepAspect',True):sx=width/w if width else 1;sy=height/h if height else 1
                    change=np.array([[sx,0,b['x']*(1-sx)],[0,sy,b['y']*(1-sy)],[0,0,1]])
                elif action in ('rotate','flip'):
                    b=bbox(n);x=op.get('x',b['x']+b['width']/2);y=op.get('y',b['y']+b['height']/2)
                    if action=='rotate':angle=math.radians(op['angle']);c,s=math.cos(angle),math.sin(angle);change=np.array([[c,-s,x-c*x+s*y],[s,c,y-s*x-c*y],[0,0,1]])
                    else:
                        sx,sy=(-1,1) if op['axis']=='x' else (1,-1);change=np.array([[sx,0,x*(1-sx)],[0,sy,y*(1-sy)],[0,0,1]])
                elif action=='align':
                    b=bbox(n);ref=bbox(self.pick(op['reference']));edge=op['edge'];axis='x' if edge in ('left','right','center_x') else 'y';dim='width' if axis=='x' else 'height';factor=1 if edge in ('right','bottom') else .5 if edge.startswith('center') else 0
                    change[0 if axis=='x' else 1,2]=ref[axis]+factor*ref[dim]-b[axis]-factor*b[dim]
                else:
                    b=bbox(n);axis=op['axis'];dim='width' if axis=='x' else 'height'
                    if cursor is None:cursor=b[axis]
                    change[0 if axis=='x' else 1,2]=cursor-b[axis];cursor+=b[dim]+op['gap']
                self.transform(n,change)
        elif action in ('set_text','set_text_style'):
            n=nodes[0]
            if n.tag not in (svg.Q('text'),svg.Q('tspan'),svg.Q('flowPara')):raise Failure('UNSUPPORTED_OBJECT','Select editable text or a tspan, not a raster image or path')
            if action=='set_text':
                if len(n):
                    spans=[c for c in n if isinstance(c.tag,str) and c.tag==svg.Q('tspan')]
                    if len(spans)==1 and len(n)==1 and not n.text:spans[0].text=op['text']
                    else:raise Failure('UNSUPPORTED_OBJECT','Select a specific tspan to preserve mixed text formatting')
                else:n.text=op['text']
            else:self.set_style(n,{k:v for key,k,v in [('font','font-family',op.get('font')),('size','font-size',str(op.get('size'))+'mm'),('color','fill',op.get('color'))] if key in op})
        elif action=='set_style':
            for n in nodes:self.set_style(n,op['style'])
        elif action in ('add_text','create_rect','create_ellipse','create_path','create_layer'):
            kind={'add_text':'text','create_rect':'rect','create_ellipse':'ellipse','create_path':'path','create_layer':'g'}[action]
            parent=self.pick(op['parent'],True) if op.get('parent') else self.root
            if parent.tag not in (svg.Q('g'),svg.Q('svg')):raise Failure('UNSUPPORTED_OBJECT','Parent must be a layer, group or SVG')
            key=self.new_id(op.get('id'));n=svg.E.SubElement(parent,svg.Q(kind),id=key)
            if action=='create_layer':n.set('{'+svg.INK+'}groupmode','layer');n.set('{'+svg.INK+'}label',op['label'])
            elif action=='create_path':parse_path(op['d']);n.set('d',op['d'])
            else:
                inverse=np.linalg.inv(self.matrix(parent))
                n.set('transform','matrix('+','.join(svg.fmt(float(x)) for x in [inverse[0,0],inverse[1,0],inverse[0,1],inverse[1,1],inverse[0,2],inverse[1,2]])+')')
                for attr in ('x','y','width','height','cx','cy','rx','ry'):
                    if attr in op:n.set(attr,str(op[attr]))
                if action=='add_text':
                    n.text=op['text'];self.set_style(n,{'font-size':str(op.get('size',3.5))+'mm','fill':op.get('color','#17191d'),'font-family':op.get('font','sans-serif')})
            if op.get('style'):self.set_style(n,op['style'])
            return [key]
        elif action in ('set_path','set_path_node'):
            n=nodes[0]
            if n.tag!=svg.Q('path') or n.get('{'+svg.INK+'}path-effect'):raise Failure('UNSUPPORTED_OBJECT','Node edits require a plain path; live path effects are preserved')
            if action=='set_path':parse_path(op['d']);n.set('d',op['d'])
            else:
                p=parse_path(n.get('d',''));index=op['index']
                if not p or index>len(p):raise Failure('INVALID_ARGUMENT','Path node index is out of range')
                value=complex(op['x'],op['y'])
                if index==0:p[0]=self._segment(p[0],start=value)
                else:p[index-1]=self._segment(p[index-1],end=value)
                if 0<index<len(p):p[index]=self._segment(p[index],start=value)
                n.set('d',p.d())
        elif action=='group':
            parent=nodes[0].getparent()
            if any(n.getparent() is not parent for n in nodes):raise Failure('INVALID_ARGUMENT','Grouping requires siblings in one layer')
            key=self.new_id(op.get('groupId'));g=svg.E.Element(svg.Q('g'),id=key);parent.insert(min(parent.index(n) for n in nodes),g)
            for n in nodes:g.append(n)
            return [*ids,key]
        elif action=='ungroup':
            for n in nodes:
                if n.tag!=svg.Q('g') or n.get('clip-path') or n.get('mask') or self.style(n).get('opacity') not in (None,'1'):
                    raise Failure('UNSUPPORTED_OBJECT','Ungrouping this object would change compositing; use native UI')
                parent=n.getparent();index=parent.index(n)
                for child in list(n):
                    if not isinstance(child.tag,str):continue
                    child.set('transform',(n.get('transform','')+' '+child.get('transform','')).strip())
                    inherited={k:v for k,v in n.attrib.items() if k in STYLE_PROPERTIES}
                    inherited.update(self.style(n))
                    for key in list(inherited):
                        if child.get(key) is not None:inherited.pop(key)
                    inherited.update(self.style(child))
                    if inherited:child.set('style',';'.join(f'{k}:{v}' for k,v in inherited.items()))
                    parent.insert(index,child);index+=1
                parent.remove(n)
        elif action=='delete':
            for n in nodes:
                key=n.get('id')
                if key and any(f'#{key}' in str(v) for other in self.root.iter() if other not in n.iter() for v in other.attrib.values()):raise Failure('UNSUPPORTED_OBJECT','Object is referenced by a clone, mask or other definition')
                n.getparent().remove(n)
        elif action=='reorder':
            for n in nodes:
                parent=n.getparent();index=parent.index(n);pos=op['position']
                parent.remove(n);parent.insert(len(parent) if pos=='front' else 0 if pos=='back' else min(len(parent),index+1) if pos=='raise' else max(0,index-1),n)
        elif action=='set_locked':
            for n in nodes:
                key='{'+svg.SOD+'}insensitive'
                if op['locked']:n.set(key,'true')
                else:n.attrib.pop(key,None);n.attrib.pop(svg.M('locked'),None)
        elif action=='set_visible':
            for n in nodes:self.set_style(n,{'display':'inline' if op['visible'] else 'none'})
        elif action=='rename_layer':nodes[0].set('{'+svg.INK+'}label',op['label'])
        elif action=='move_to_layer':
            layer=self.pick(op['layer'],True)
            if layer.tag!=svg.Q('g'):raise Failure('UNSUPPORTED_OBJECT','Target must be a layer or group')
            for n in nodes:
                if layer is n or n in layer.iterancestors():raise Failure('INVALID_ARGUMENT','Cannot move an ancestor into its descendant')
                world=self.matrix(n);layer.append(n)
                local=np.linalg.inv(self.matrix(layer))@world;n.set('transform','matrix('+','.join(svg.fmt(float(x)) for x in [local[0,0],local[1,0],local[0,1],local[1,1],local[0,2],local[1,2]])+')')
        elif action in ('set_clip','set_mask','release_clip','release_mask'):
            attr='clip-path' if action.endswith('clip') else 'mask'
            if action.startswith('release'):
                for n in nodes:n.attrib.pop(attr,None)
            else:
                source=self.pick(op['source']);expected=svg.Q('clipPath' if attr=='clip-path' else 'mask')
                if source.tag!=expected:raise Failure('UNSUPPORTED_OBJECT','Source must be an existing clipPath or mask definition')
                for n in nodes:n.set(attr,f'url(#{source.get("id")})')
        elif action=='boolean':
            if self.engine is None:raise Failure('DEPENDENCY_MISSING','Boolean operations require the native engine')
            if any(n.get('{'+svg.INK+'}path-effect') for n in nodes):raise Failure('UNSUPPORTED_OBJECT','Convert path effects explicitly in native UI before boolean operations')
            for n in nodes:
                if not n.get('id'):n.set('id',self.new_id())
                if not svg.ID.fullmatch(n.get('id')):raise Failure('INVALID_ARGUMENT','Boolean operations require IDs without native action separators')
            selected=','.join(n.get('id') for n in nodes)
            native={'union':'path-union','difference':'path-difference','intersection':'path-intersection','object_to_path':'object-to-path'}[op['operation']]
            with tempfile.TemporaryDirectory() as temp:
                source=Path(temp)/'candidate.svg';source.write_bytes(self.relocate(self.encode(),self.base,source.parent,self.workspace))
                self.engine.run(['--app-id-tag=gewu-candidate','--batch-process',f'--actions=select-by-id:{selected};{native};export-overwrite;export-filename:{source};export-do',source])
                self.root=svg.parse(self.relocate(source.read_bytes(),source.parent,self.base,self.workspace))
        elif action=='import_panel':
            source=svg.inside(self.workspace,op['path']);raw=source.read_bytes()
            if source.suffix.lower() in ('.pdf','.jpg','.jpeg'):
                if self.engine is None:raise Failure('DEPENDENCY_MISSING','PDF/JPEG import requires Inkscape')
                with tempfile.TemporaryDirectory() as temp:
                    target=Path(temp)/'import.svg'
                    if source.suffix.lower()=='.pdf':
                        strategy='keep' if op.get('text','preserve')=='preserve' else 'draw-all'
                        self.engine.run(['--app-id-tag=gewu-import',f'--pages={op.get("page",1)}',f'--pdf-font-strategy={strategy}','--export-type=svg',f'--export-filename={target}',source]);raw=target.read_bytes()
                    else:
                        from PIL import Image
                        from io import BytesIO
                        out=BytesIO();Image.open(source).save(out,format='PNG');raw=out.getvalue()
                if source.suffix.lower()!='.pdf':
                    with tempfile.TemporaryDirectory(dir=self.workspace) as temp:
                        p=Path(temp)/'import.png';p.write_bytes(raw)
                        return svg.apply(self.root,{**op,'path':p.relative_to(self.workspace).as_posix()},self.workspace,lambda _:self.measure())
            if source.suffix.lower()=='.pdf':
                with tempfile.TemporaryDirectory(dir=self.workspace) as temp:
                    p=Path(temp)/'import.svg';p.write_bytes(raw)
                    return svg.apply(self.root,{k:v for k,v in {**op,'path':p.relative_to(self.workspace).as_posix()}.items() if k not in ('page','text')},self.workspace,lambda _:self.measure())
            return svg.apply(self.root,op,self.workspace,lambda _:self.measure())
        elif action in ('set_page','add_page','rename_page'):return svg.apply(self.root,op,self.workspace,None)
        elif action=='add_guide':
            view=self.root.find('{'+svg.SOD+'}namedview')
            if view is None:view=svg.E.SubElement(self.root,'{'+svg.SOD+'}namedview')
            _,_,sx,sy,_,_=svg.page_frame(self.root)
            svg.E.SubElement(view,'{'+svg.SOD+'}guide',position=f'{op["x"]/sx},{op["y"]/sy}',orientation='0,1' if op['orientation']=='horizontal' else '1,0')
        return ids

    @staticmethod
    def _segment(segment,start=None,end=None):
        start=segment.start if start is None else start;end=segment.end if end is None else end
        if isinstance(segment,Line):return Line(start,end)
        if isinstance(segment,CubicBezier):return CubicBezier(start,segment.control1,segment.control2,end)
        if isinstance(segment,QuadraticBezier):return QuadraticBezier(start,segment.control,end)
        if isinstance(segment,Arc):return Arc(start,segment.radius,segment.rotation,segment.large_arc,segment.sweep,end)
        raise Failure('UNSUPPORTED_OBJECT','Unsupported path segment')
