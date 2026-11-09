"""Dashboard-only persistence. No assistant execution or production registry access."""
import json,re,sqlite3,threading,uuid
from contextlib import contextmanager,closing
from datetime import datetime,timezone
from reader import HERE

def safe_text(text):
    # Public job/target identifiers and SHA256 provenance are not credentials.
    # Match recognizable secret patterns; arbitrary long words caused false positives.
    if re.search(r'(?i)(?:\bsk-[\w-]{12,}|\bAIza[\w-]{20,}|(?:api[_ -]?key|password|token|secret|authorization)\s*[:=]\s*\S+|bearer\s+\S+)',text):
        raise ValueError('Do not include credentials or secrets in conversations.')
    return text

class Conversations:
    def __init__(self,path=None):
        self.path=path or HERE/'state/conversations.sqlite3'
        if not self.path.resolve().is_relative_to(HERE.resolve()):raise ValueError('Conversation storage must remain inside Dashboard')
        self.guard=threading.Lock();self.busy=set()
    @contextmanager
    def db(self):
        with closing(sqlite3.connect(self.path,timeout=3)) as db:
            db.row_factory=sqlite3.Row
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('CREATE TABLE IF NOT EXISTS conversations(id TEXT PRIMARY KEY,title TEXT NOT NULL,updated TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS turns(id INTEGER PRIMARY KEY,conversation TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,question TEXT NOT NULL,response TEXT NOT NULL,context TEXT NOT NULL,created TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS memories(conversation TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,summary TEXT NOT NULL)')
            with db:yield db
    def key(self,key):
        if not re.fullmatch('[a-f0-9]{32}',key):raise ValueError('Invalid conversation')
        return key
    @contextmanager
    def turn(self,key):
        self.key(key)
        with self.guard:
            if key in self.busy:raise ValueError('This conversation is processing a message.')
            self.busy.add(key)
        try:yield
        finally:
            with self.guard:self.busy.discard(key)
    def listing(self):
        with self.db() as db:return [dict(r) for r in db.execute('SELECT * FROM conversations ORDER BY updated DESC LIMIT 100')]
    def create(self):
        key=uuid.uuid4().hex
        with self.db() as db:
            if db.execute('SELECT count(*) FROM conversations').fetchone()[0]>=100:raise ValueError('Conversation limit reached; delete an unwanted conversation first.')
            db.execute('INSERT INTO conversations VALUES(?,?,?)',(key,'New research chat',datetime.now(timezone.utc).isoformat()))
        return self.get(key)
    def get(self,key):
        with self.db() as db:
            row=db.execute('SELECT * FROM conversations WHERE id=?',(self.key(key),)).fetchone()
            if row is None:raise ValueError('Conversation not found')
            turns=[dict(question=r['question'],response=json.loads(r['response']),context=json.loads(r['context']),created=r['created']) for r in db.execute('SELECT * FROM turns WHERE conversation=? ORDER BY id',(key,))]
        return dict(row)|{'turns':turns}
    def rename(self,key,title):
        title=safe_text(title.strip())
        if not title or len(title)>120:raise ValueError('Use a title of 1–120 characters.')
        with self.turn(key),self.db() as db:
            if db.execute('UPDATE conversations SET title=? WHERE id=?',(title,key)).rowcount!=1:raise ValueError('Conversation not found')
        return self.get(key)
    def delete(self,key):
        with self.turn(key),self.db() as db:
            db.execute('DELETE FROM conversations WHERE id=?',(key,))
        return {'deleted':True}
    def clear(self,key):
        with self.turn(key),self.db() as db:
            if not db.execute('SELECT 1 FROM conversations WHERE id=?',(key,)).fetchone():raise ValueError('Conversation not found')
            db.execute('DELETE FROM turns WHERE conversation=?',(key,))
            db.execute('DELETE FROM memories WHERE conversation=?',(key,))
        return self.get(key)
    def remember(self,key,summary):
        with self.db() as db:
            db.execute('INSERT OR REPLACE INTO memories VALUES(?,?)',(self.key(key),summary))
    def append(self,key,question,response,context):
        safe_text(question);safe_text(response['answer'])
        # Only vetted display fields and an opaque audit link are persisted here.
        response={k:v for k,v in response.items() if k in ('answer','rows','chart','charts','limitations','engine','model','provider','fallback_used','tool_trace','tool_evidence','verified_findings','audit_ref','control_request','control_state','worker_state','control_available','ownership_verified','operation_status','receipt','step_result','failure_reason','collection_scope')}
        payload=json.dumps(response,allow_nan=False)
        if len(payload)>300000:raise ValueError('Response exceeds conversation storage limit')
        now=datetime.now(timezone.utc).isoformat()
        with self.db() as db:
            if db.execute('SELECT count(*) FROM turns WHERE conversation=?',(key,)).fetchone()[0]>=100:raise ValueError('Start a new conversation; this chat has reached 100 turns.')
            db.execute('INSERT INTO turns(conversation,question,response,context,created) VALUES(?,?,?,?,?)',(key,question,payload,json.dumps(context),now))
            db.execute("UPDATE conversations SET updated=?,title=CASE WHEN title='New research chat' THEN ? ELSE title END WHERE id=?",(now,question[:70],key))

store=Conversations()
