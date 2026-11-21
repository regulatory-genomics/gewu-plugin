"""Machine tool catalog for both the GeWu host and adapter validation."""
def obj(properties=None, required=()):
    return {'type':'object','properties':properties or {},'required':list(required),'additionalProperties':False}


def string(length=256): return {'type':'string','minLength':1,'maxLength':length}
def enum(*values): return {'enum':list(values)}
def array(item, maximum, minimum=0): return {'type':'array','items':item,'minItems':minimum,'maxItems':maximum}


REV={'type':['string','null'],'maxLength':128}
ID=string(128)
PATH=string(512)
IDS=array(ID,100,1)
NUM={'type':'number','minimum':-100000,'maximum':100000}
POS={'type':'number','exclusiveMinimum':0,'maximum':2000}
BOOLEAN={'type':'boolean'}
COUNT={'type':'integer','minimum':0}
COLOR={'type':'string','pattern':'^#[0-9a-fA-F]{6}$'}
FONT={'type':'string','pattern':r"^[\w ,'-]{1,100}$"}
TEXT={'type':'string','maxLength':2048}
PAGE={'type':'integer','minimum':1,'maximum':100}
XY={'x':NUM,'y':NUM}
SIZE={'width':POS,'height':POS,'margin':NUM}
STYLE={'size':POS,'font':FONT,'color':COLOR}


def operation(name, fields, *required):
    return obj({'op':{'const':name},**fields},('op',*required))


OPERATIONS=[
    operation('create_document',SIZE,'width','height'),
    operation('set_page',SIZE,'width','height'),
    operation('import_panel',{'id':ID,'path':PATH,'width':POS,**XY},'path'),
    operation('adopt_objects',{'ids':IDS},'ids'),
    operation('move',{'ids':IDS,'dx':NUM,'dy':NUM},'ids','dx','dy'),
    operation('resize',{'ids':IDS,'width':POS,'size':POS,'dimension':enum('width','height')},'ids'),
    operation('align',{'ids':IDS,'edge':enum('left','right','top','bottom','center_x','center_y'),'reference':ID},'ids','edge','reference'),
    operation('distribute',{'ids':IDS,'axis':enum('x','y'),'gap':NUM},'ids','axis','gap'),
    operation('equalize',{'ids':IDS,'dimension':enum('width','height'),'reference':ID},'ids','reference'),
    operation('replace_panel',{'id':ID,'path':PATH},'id'),
    operation('add_text',{'id':ID,'text':TEXT,**XY,**STYLE},'text','x','y'),
    operation('set_text',{'id':ID,'text':TEXT},'id','text'),
    operation('set_text_style',{'id':ID,**STYLE},'id'),
    operation('group',{'ids':IDS,'groupId':ID},'ids'),
    operation('ungroup',{'ids':IDS},'ids'),
    operation('reorder',{'ids':IDS,'position':enum('front','back')},'ids','position'),
    operation('set_locked',{'ids':IDS,'locked':BOOLEAN},'ids','locked'),
    operation('delete',{'ids':IDS},'ids'),
    operation('add_page',{'width':POS,'height':POS,'label':string(100)},'width','height'),
    operation('rename_page',{'page':PAGE,'label':string(100)},'page','label'),
    operation('save_rule',{'name':ID,'operations':array({'type':'object'},30,1)},'name','operations'),
    operation('apply_rule',{'name':ID},'name'),
    operation('remove_rule',{'name':ID},'name'),
    operation('set_preset',{'name':ID,**STYLE,'margin':NUM},'name'),
    operation('apply_preset',{'name':ID,'ids':IDS},'name'),
    operation('set_inner_text',{'panel':ID,'id':ID,'text':TEXT},'panel','id','text'),
    operation('set_inner_text_style',{'panel':ID,'id':ID,**STYLE},'panel','id'),
]
for item in OPERATIONS:
    if item['properties']['op']['const']=='resize':
        item['anyOf']=[{'required':['width']},{'required':['size']}]


def tool(name, description, args=None, required=(), *, write=False, view=False, private=False, output=()):
    return {'name':name,'description':description,'effect':'write' if write else 'read','target':'view' if view else 'backend',
            'exposeToAgent':not private,'inputSchema':obj(args,required),'outputSchema':{'type':'object','required':list(output)}}


EXPECTED={'expectedRevision':REV}
PAGE_ARGS={'limit':{'type':'integer','minimum':1,'maximum':50},'offset':COUNT,'revision':REV}
TOOLS=[
    tool('diagnose','Read native Inkscape version and figure binding without changing SVG.',output=('version','document')),
    tool('state','Read revision, page geometry in mm and a bounded panel list. Use inspect to page.',PAGE_ARGS,output=('revision','exists','objects','total','nextOffset')),
    tool('inspect','Read explicit top-level objects or one revision-pinned page of objects.',{**PAGE_ARGS,'ids':IDS},output=('revision','objects','total','nextOffset')),
    tool('selection','Read exact selected IDs and current revision from the focused figure view.',view=True,output=('ids','revision')),
    tool('edit','Commit one undoable figure edit. Read current revision and explicit object IDs first; coordinates are mm, x right and y down.',
         {**EXPECTED,'operations':array({'oneOf':OPERATIONS},100,1)},('expectedRevision','operations'),write=True,output=('revision','affected','canUndo','canRedo')),
    *[tool(name,f'{name.capitalize()} one complete shared history step.',EXPECTED,('expectedRevision',),write=True,output=('revision','affected')) for name in ('undo','redo')],
    tool('reload','Validate a changed external SVG and acknowledge its observedHash; start a new history epoch.',
         {**EXPECTED,'observedHash':string(64)},('expectedRevision','observedHash'),write=True,output=('revision',)),
    tool('render','Render an exact figure revision as a native PNG; pages are 1-based.',{'revision':REV,'page':PAGE},('revision',),output=('revision','preview')),
    tool('export','Create an immutable editable SVG, PDF or PNG export at an exact figure revision.',
         {'revision':REV,'format':enum('svg','pdf','png'),'dpi':{'type':'integer','minimum':24,'maximum':1200},'text':enum('preserve','paths'),'page':PAGE},
         ('revision','format'),write=True,output=('revision','artifact','provenance')),
    tool('sources','Find changed, missing and unchanged imported panel source files.',PAGE_ARGS,output=('revision','sources','total','nextOffset')),
    tool('check_layout','Report off-page, overlap, low raster DPI and unlabelled panels; review warnings visually.',PAGE_ARGS,output=('revision','issues','total','nextOffset')),
    tool('inspect_inner','List original IDs of simple editable text inside one panel. No scientific content is changed.',
         {**PAGE_ARGS,'panel':ID},('panel',),output=('revision','objects','total','nextOffset')),
    tool('checkout','Freeze embedded writes and create an editable copy for desktop Inkscape.',EXPECTED,('expectedRevision',),write=True,output=('path','revision')),
    tool('checkin','Accept the saved desktop copy as one undo step or cancel checkout without changing the figure.',
         {**EXPECTED,'accept':BOOLEAN},('expectedRevision','accept'),write=True,output=('revision',)),
    tool('handoff','Publish a versioned figure for Paper or Manimble. Optionally place it in an existing recipient project without changing document references.',
         {'revision':REV,'consumer':enum('paper','manimble'),'page':PAGE,'project':PATH,'format':enum('png','svg','pdf')},('revision','consumer'),write=True,output=('artifact','figureId','consumer','revision','manifestPath')),
    tool('handoff_status','Check whether a saved handoff manifest still matches this figure without writing either project.',
         {'path':PATH},('path',),output=('figureId','revision','publishedRevision','status')),
    tool('live_state','Read the unsaved native figure revision, exact selection and bounded native bounds. Requires a configured desktop checkout.',
         PAGE_ARGS,output=('revision','selection','objects','total','nextOffset')),
    tool('live_render','Render the unsaved native document at a given live revision.',
         {'revision':REV,'page':PAGE},('revision',),output=('revision','preview')),
    tool('live_follow','Read desktop selection and only rerender when the native revision changes.',
         {'knownRevision':REV,'page':PAGE},output=('revision','selection','selectionCount')),
    tool('live_edit','Apply a bounded geometry/text batch to the native in-memory document. The native user can undo it in Inkscape.',
         {**EXPECTED,'operations':array({'oneOf':[x for x in OPERATIONS if x['properties']['op']['const'] in
                ('move','resize','align','distribute','equalize','add_text','set_text','set_text_style','set_inner_text','set_inner_text_style')]},20,1)},
         ('expectedRevision','operations'),write=True,output=('revision','selection','affected')),
    *[tool('view.'+name,'Private view lifecycle.',view=True,private=True) for name in ('attach','detach')],
    tool('view.selection','Update bound-view selection.',{'ids':array(ID,100),'sequence':COUNT,'revision':REV},('ids','sequence','revision'),view=True,private=True),
]

# Per-operation schemas are discoverable without expanding the whole registry in every model turn.
NATIVE_TOOLS=[
    tool('diagnose','Read the native runtime, connection and save diagnostics.',output=('version','document')),
    tool('capabilities','List supported native operations; provide operation to retrieve its exact schema and example.',{'operation':ID},output=('operations',)),
    tool('state','Read the live native document revision, native selection, save state and bounded object list.',PAGE_ARGS,output=('revision','exists','objects','total','nextOffset')),
    tool('inspect','Query the complete native object hierarchy, including nested text and paths.',
         {**PAGE_ARGS,'ids':IDS,'parent':ID,'kind':ID,'text':TEXT,'fields':array(enum('style','path','bbox'),3)},output=('revision','objects','total','nextOffset')),
    tool('selection','Read a page of actual native selected IDs and the live revision.',PAGE_ARGS,output=('ids','revision')),
    tool('select','Select native objects by their IDs in the same Inkscape window.',{**EXPECTED,'ids':array(ID,100)},('expectedRevision','ids'),write=True,output=('revision','ids')),
    tool('edit','Commit one native undoable batch. Read revision and capabilities for the required operation schema first. The user can undo the entire batch in Inkscape.',
         {**EXPECTED,'requestId':ID,'operations':array({'type':'object','properties':{'op':ID},'required':['op']},50,1)},('expectedRevision','operations'),write=True,output=('revision','affected')),
    *[tool(name,f'{name.capitalize()} one step of the shared native history.',EXPECTED,('expectedRevision',),write=True,output=('revision',)) for name in ('undo','redo','save')],
    tool('request_status','Check an edit requestId before retrying after a lost response.',{'requestId':ID},('requestId',),output=('status',)),
    *[item for item in TOOLS if item['name'] in ('render','export','handoff','handoff_status','view.attach','view.detach')],
]
