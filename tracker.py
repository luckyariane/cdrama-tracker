"""Local C-drama tracker backed by SQLite. Run with: python tracker.py"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import base64
import binascii
import json
import random
import secrets
import socket
import sqlite3
from datetime import date
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DB = ROOT / "cdrama_tracker.sqlite3"
POSTER_DIR = ROOT / "posters"
PORT = 8765
THEME_COUNT = 8
POSTER_TYPES = {'image/jpeg':'jpg','image/png':'png','image/webp':'webp','image/gif':'gif','image/heic':'heic','image/heif':'heif'}
MAX_POSTER_BYTES = 8 * 1024 * 1024


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS dramas (
          id INTEGER PRIMARY KEY, title TEXT NOT NULL, url TEXT NOT NULL DEFAULT '',
          episode INTEGER NOT NULL DEFAULT 0, color_theme INTEGER NOT NULL DEFAULT 0,
          episode_count INTEGER CHECK(episode_count BETWEEN 1 AND 9999),
          show_score REAL CHECK(show_score BETWEEN 0 AND 10),
          romance_rating INTEGER NOT NULL DEFAULT 0 CHECK(romance_rating BETWEEN 0 AND 4),
          action_rating INTEGER NOT NULL DEFAULT 0 CHECK(action_rating BETWEEN 0 AND 4),
          character_rating INTEGER NOT NULL DEFAULT 0 CHECK(character_rating BETWEEN 0 AND 4),
          plot_rating INTEGER NOT NULL DEFAULT 0 CHECK(plot_rating BETWEEN 0 AND 4),
          visuals_rating INTEGER NOT NULL DEFAULT 0 CHECK(visuals_rating BETWEEN 0 AND 4),
          hashtags TEXT NOT NULL DEFAULT '',
          genre TEXT NOT NULL DEFAULT '', cast_1 TEXT NOT NULL DEFAULT '', cast_2 TEXT NOT NULL DEFAULT '',
          cast_3 TEXT NOT NULL DEFAULT '', cast_4 TEXT NOT NULL DEFAULT '', poster_url TEXT NOT NULL DEFAULT '',
          poster_file TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now')),
          completed_at TEXT, watch_start_date TEXT NOT NULL DEFAULT (date('now')), watch_end_date TEXT,
          updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%d %H:%M:%f','now'))
        );
        CREATE TABLE IF NOT EXISTS characters (
          id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
          short_form TEXT NOT NULL DEFAULT '', name TEXT NOT NULL, notes TEXT NOT NULL DEFAULT '',
          sort_order INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS plot_points (
          id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
          text TEXT NOT NULL, episode INTEGER NOT NULL DEFAULT 0,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS moments (
          id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
          episode INTEGER, emoji TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 0 AND 4),
          text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS progress_activity (
          id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS drama_sort ON dramas(updated_at DESC);
        CREATE INDEX IF NOT EXISTS characters_sort ON characters(drama_id, id DESC);
        CREATE INDEX IF NOT EXISTS plot_sort ON plot_points(drama_id, id DESC);
        CREATE INDEX IF NOT EXISTS moments_sort ON moments(drama_id, id DESC);
        CREATE INDEX IF NOT EXISTS progress_activity_sort ON progress_activity(drama_id, id DESC);
        """)
        # Add the episode column to databases created by earlier tracker versions.
        columns = {row['name'] for row in db.execute('PRAGMA table_info(plot_points)')}
        if 'episode' not in columns:
            db.execute('ALTER TABLE plot_points ADD COLUMN episode INTEGER NOT NULL DEFAULT 0')
        character_columns = {row['name'] for row in db.execute('PRAGMA table_info(characters)')}
        if 'short_form' not in character_columns:
            db.execute("ALTER TABLE characters ADD COLUMN short_form TEXT NOT NULL DEFAULT ''")
        if 'notes' not in character_columns:
            db.execute("ALTER TABLE characters ADD COLUMN notes TEXT NOT NULL DEFAULT ''")
        if 'sort_order' not in character_columns:
            db.execute('ALTER TABLE characters ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0')
            for drama in db.execute('SELECT DISTINCT drama_id FROM characters'):
                character_ids=[r['id'] for r in db.execute('SELECT id FROM characters WHERE drama_id=? ORDER BY id',(drama['drama_id'],))]
                for position,character_id in enumerate(character_ids):
                    db.execute('UPDATE characters SET sort_order=? WHERE id=?',(position,character_id))
        drama_columns = {row['name'] for row in db.execute('PRAGMA table_info(dramas)')}
        if 'episode_count' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN episode_count INTEGER CHECK(episode_count BETWEEN 1 AND 9999)')
            drama_columns.add('episode_count')
        if 'show_score' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN show_score REAL CHECK(show_score BETWEEN 0 AND 10)')
            if 'show_rating' in drama_columns:
                db.execute('UPDATE dramas SET show_score=show_rating*2.0 WHERE show_rating IS NOT NULL')
            drama_columns.add('show_score')
        for column in ('romance_rating','action_rating','character_rating','plot_rating','visuals_rating'):
            if column not in drama_columns:
                db.execute(f'ALTER TABLE dramas ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0 CHECK({column} BETWEEN 0 AND 4)')
        drama_columns = {row['name'] for row in db.execute('PRAGMA table_info(dramas)')}
        if 'hashtags' not in drama_columns:
            db.execute("ALTER TABLE dramas ADD COLUMN hashtags TEXT NOT NULL DEFAULT ''")
        drama_columns = {row['name'] for row in db.execute('PRAGMA table_info(dramas)')}
        for column in ('genre','cast_1','cast_2','cast_3','cast_4','poster_url','poster_file'):
            if column not in drama_columns:
                db.execute(f"ALTER TABLE dramas ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
        drama_columns = {row['name'] for row in db.execute('PRAGMA table_info(dramas)')}
        if 'created_at' not in drama_columns:
            db.execute("ALTER TABLE dramas ADD COLUMN created_at TEXT NOT NULL DEFAULT ''")
            db.execute("""UPDATE dramas SET created_at=coalesce(
              (SELECT min(created_at) FROM moments WHERE moments.drama_id=dramas.id),
              (SELECT min(created_at) FROM progress_activity WHERE progress_activity.drama_id=dramas.id),
              strftime('%Y-%m-%d %H:%M:%f','now')) WHERE created_at=''""")
        if 'completed_at' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN completed_at TEXT')
        if 'watch_start_date' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN watch_start_date TEXT')
            db.execute("UPDATE dramas SET watch_start_date=coalesce(date(created_at),date('now')) WHERE watch_start_date IS NULL")
        if 'watch_end_date' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN watch_end_date TEXT')
            db.execute('UPDATE dramas SET watch_end_date=date(completed_at) WHERE completed_at IS NOT NULL')
        db.execute("UPDATE dramas SET completed_at=updated_at WHERE show_score IS NOT NULL AND completed_at IS NULL")
        if 'color_theme' not in drama_columns:
            db.execute('ALTER TABLE dramas ADD COLUMN color_theme INTEGER NOT NULL DEFAULT 0')
            ids = [row['id'] for row in db.execute('SELECT id FROM dramas ORDER BY id')]
            themes = list(range(THEME_COUNT))
            random.SystemRandom().shuffle(themes)
            for index, drama_id in enumerate(ids):
                db.execute('UPDATE dramas SET color_theme=? WHERE id=?', (themes[index % THEME_COUNT], drama_id))
        moment_sql = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='moments'").fetchone()['sql']
        if 'BETWEEN 1 AND 4' in moment_sql or 'episode INTEGER NOT NULL' in moment_sql:
            db.execute('DROP INDEX IF EXISTS moments_sort')
            db.execute('ALTER TABLE moments RENAME TO moments_old')
            db.execute("""CREATE TABLE moments (
              id INTEGER PRIMARY KEY, drama_id INTEGER NOT NULL REFERENCES dramas(id) ON DELETE CASCADE,
              episode INTEGER, emoji TEXT NOT NULL, rating INTEGER NOT NULL CHECK(rating BETWEEN 0 AND 4),
              text TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
            db.execute('INSERT INTO moments(id,drama_id,episode,emoji,rating,text,created_at) SELECT id,drama_id,episode,emoji,rating,text,created_at FROM moments_old')
            db.execute('DROP TABLE moments_old')
            db.execute('CREATE INDEX moments_sort ON moments(drama_id, id DESC)')
        # Fold legacy plot points into the journal exactly once, preserving their timestamps.
        db.execute("""INSERT INTO moments(drama_id,episode,emoji,rating,text,created_at)
          SELECT drama_id,episode,'📖',0,text,created_at FROM plot_points ORDER BY id""")
        db.execute('DELETE FROM plot_points')
        db.execute("UPDATE moments SET emoji='🖐️' WHERE emoji='🔵'")
        db.execute("UPDATE moments SET emoji='💋' WHERE emoji IN ('❤️','👄')")


def rowdict(row):
    return dict(row) if row else None


def remove_saved_poster(value):
    if not value or not value.startswith('/posters/'):
        return
    filename=value.rsplit('/',1)[-1]
    candidate=POSTER_DIR/filename
    if candidate.name==filename and candidate.parent.resolve()==POSTER_DIR.resolve() and candidate.is_file():
        candidate.unlink()


class Handler(BaseHTTPRequestHandler):
    def send_json(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)

    def body(self):
        length = int(self.headers.get('Content-Length', 0))
        return json.loads(self.rfile.read(length) or b'{}')

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/dramas':
            with connect() as db:
                rows = db.execute("""SELECT d.*, (SELECT COUNT(*) FROM moments m WHERE m.drama_id=d.id) moment_count,
                  (SELECT COUNT(*) FROM characters c WHERE c.drama_id=d.id) character_count
                  FROM dramas d ORDER BY updated_at DESC, id DESC""").fetchall()
            return self.send_json([rowdict(r) for r in rows])
        if path.startswith('/api/dramas/'):
            try: drama_id = int(path.split('/')[3])
            except (ValueError, IndexError): return self.send_json({'error':'Not found'}, 404)
            with connect() as db:
                drama = db.execute('SELECT * FROM dramas WHERE id=?', (drama_id,)).fetchone()
                if not drama: return self.send_json({'error':'Not found'}, 404)
                result = rowdict(drama)
                result['characters'] = [rowdict(r) for r in db.execute('SELECT * FROM characters WHERE drama_id=? ORDER BY sort_order,id', (drama_id,))]
                result['moments'] = [rowdict(r) for r in db.execute('SELECT * FROM moments WHERE drama_id=? ORDER BY (episode IS NULL) ASC, episode DESC, created_at DESC, id DESC', (drama_id,))]
                result['progress_activity'] = [rowdict(r) for r in db.execute('SELECT * FROM progress_activity WHERE drama_id=? ORDER BY id DESC', (drama_id,))]
            return self.send_json(result)
        if path == '/' or path == '/index.html':
            data = (ROOT / 'tracker.html').read_bytes()
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); return self.wfile.write(data)
        if path == '/style.css' or path == '/app.js':
            data = (ROOT / path.lstrip('/')).read_bytes()
            self.send_response(200); self.send_header('Content-Type','text/css; charset=utf-8' if path.endswith('.css') else 'text/javascript; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); return self.wfile.write(data)
        if path.startswith('/posters/'):
            filename=path.rsplit('/',1)[-1]
            poster_path=POSTER_DIR / filename
            content_type={'jpg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif','heic':'image/heic','heif':'image/heif'}.get(poster_path.suffix.lower().lstrip('.'))
            if not content_type or poster_path.name!=filename or poster_path.parent.resolve()!=POSTER_DIR.resolve() or not poster_path.is_file():
                return self.send_json({'error':'Not found'},404)
            data=poster_path.read_bytes()
            self.send_response(200);self.send_header('Content-Type',content_type);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','public, max-age=86400');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();return self.wfile.write(data)
        self.send_json({'error':'Not found'}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        data = self.body()
        with connect() as db:
            parts = urlparse(self.path).path.strip('/').split('/')
            if len(parts)==6 and parts[:2]==['api','dramas'] and parts[3]=='characters' and parts[5]=='move':
                try: did,character_id=int(parts[2]),int(parts[4])
                except ValueError: return self.send_json({'error':'Not found'},404)
                direction=data.get('direction')
                if direction not in ('up','down'): return self.send_json({'error':'Choose up or down.'},400)
                ids=[row['id'] for row in db.execute('SELECT id FROM characters WHERE drama_id=? ORDER BY sort_order,id',(did,))]
                if character_id not in ids: return self.send_json({'error':'Character not found.'},404)
                old=ids.index(character_id); new=max(0,min(len(ids)-1,old+(-1 if direction=='up' else 1)))
                ids[old],ids[new]=ids[new],ids[old]
                for position,item_id in enumerate(ids): db.execute('UPDATE characters SET sort_order=? WHERE id=?',(position,item_id))
                db.execute("UPDATE dramas SET updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(did,))
                return self.send_json({'ok':True})
            if path == '/api/dramas':
                title = str(data.get('title','')).strip()
                if not title: return self.send_json({'error':'Please enter a title.'}, 400)
                try: episode_count=int(data.get('episode_count'))
                except (TypeError,ValueError): return self.send_json({'error':'Enter the total number of episodes.'},400)
                if episode_count not in range(1,10000): return self.send_json({'error':'Episode count must be between 1 and 9999.'},400)
                url = str(data.get('url','')).strip()
                if url and urlparse(url).scheme not in ('http','https'):
                    return self.send_json({'error':'Drama links must start with http:// or https://.'},400)
                used = {row['color_theme'] for row in db.execute('SELECT color_theme FROM dramas')}
                available = [theme for theme in range(THEME_COUNT) if theme not in used]
                theme = random.choice(available or list(range(THEME_COUNT)))
                cur = db.execute("INSERT INTO dramas(title,url,color_theme,episode_count) VALUES(?,?,?,?)", (title, url, theme, episode_count))
                return self.send_json(rowdict(db.execute('SELECT * FROM dramas WHERE id=?',(cur.lastrowid,)).fetchone()), 201)
            parts = path.strip('/').split('/')
            if len(parts) == 4 and parts[0] == 'api' and parts[1] == 'dramas':
                try: did = int(parts[2])
                except ValueError: return self.send_json({'error':'Not found'},404)
                if not db.execute('SELECT id FROM dramas WHERE id=?',(did,)).fetchone(): return self.send_json({'error':'Not found'},404)
                kind = parts[3]
                if kind == 'poster-upload':
                    content_type=str(data.get('content_type','')).lower().split(';',1)[0].strip()
                    filename_hint=str(data.get('filename','')).lower()
                    if content_type not in POSTER_TYPES:
                        extension_hint=filename_hint.rsplit('.',1)[-1] if '.' in filename_hint else ''
                        content_type={'jpg':'image/jpeg','jpeg':'image/jpeg','png':'image/png','webp':'image/webp','gif':'image/gif','heic':'image/heic','heif':'image/heif'}.get(extension_hint,'')
                    extension=POSTER_TYPES.get(content_type)
                    if not extension: return self.send_json({'error':'Choose a JPEG, PNG, WebP, GIF, HEIC, or HEIF image.'},400)
                    try: image_bytes=base64.b64decode(data.get('data',''),validate=True)
                    except (binascii.Error,ValueError,TypeError): return self.send_json({'error':'The poster image could not be read.'},400)
                    if not image_bytes or len(image_bytes)>MAX_POSTER_BYTES: return self.send_json({'error':'Poster images must be 8 MB or smaller.'},400)
                    heif_brands=(b'heic',b'heix',b'hevc',b'hevx',b'mif1',b'msf1')
                    signatures={'image/jpeg':image_bytes.startswith(b'\xff\xd8\xff'),'image/png':image_bytes.startswith(b'\x89PNG\r\n\x1a\n'),'image/gif':image_bytes.startswith((b'GIF87a',b'GIF89a')),'image/webp':len(image_bytes)>=12 and image_bytes.startswith(b'RIFF') and image_bytes[8:12]==b'WEBP','image/heic':len(image_bytes)>=12 and image_bytes[4:8]==b'ftyp' and image_bytes[8:12] in heif_brands,'image/heif':len(image_bytes)>=12 and image_bytes[4:8]==b'ftyp' and image_bytes[8:12] in heif_brands}
                    if not signatures[content_type]: return self.send_json({'error':'The selected file does not match its image type.'},400)
                    POSTER_DIR.mkdir(parents=True,exist_ok=True)
                    stored_name=f'drama_{did}_{secrets.token_hex(10)}.{extension}'
                    (POSTER_DIR/stored_name).write_bytes(image_bytes)
                    poster_file=f'/posters/{stored_name}'
                    previous=db.execute('SELECT poster_file FROM dramas WHERE id=?',(did,)).fetchone()['poster_file']
                    db.execute("UPDATE dramas SET poster_file=?,poster_url='',updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(poster_file,did))
                    remove_saved_poster(previous)
                    return self.send_json({'poster_file':poster_file})
                if kind == 'theme':
                    try: theme = int(data.get('color_theme'))
                    except (TypeError, ValueError): return self.send_json({'error':'Invalid color theme.'},400)
                    if theme not in range(THEME_COUNT): return self.send_json({'error':'Invalid color theme.'},400)
                    db.execute("UPDATE dramas SET color_theme=? WHERE id=?",(theme,did))
                    return self.send_json({'color_theme':theme})
                if kind == 'episode':
                    episode = max(0, int(data.get('episode',0)))
                    previous = db.execute('SELECT episode FROM dramas WHERE id=?',(did,)).fetchone()['episode']
                    db.execute("UPDATE dramas SET episode=?,updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(episode,did))
                    if episode != previous:
                        db.execute('INSERT INTO progress_activity(drama_id) VALUES(?)',(did,))
                    return self.send_json({'episode':episode})
                if kind in ('characters','plot_points'):
                    value = str(data.get('name' if kind == 'characters' else 'text','')).strip()
                    if not value: return self.send_json({'error':'Please enter some text.'},400)
                    if kind == 'plot_points':
                        current = db.execute('SELECT episode FROM dramas WHERE id=?',(did,)).fetchone()['episode']
                        episode = max(0, int(data.get('episode',current)))
                        db.execute('INSERT INTO plot_points(drama_id,text,episode) VALUES(?,?,?)',(did,value,episode))
                    else:
                        short_form=str(data.get('short_form','')).strip()
                        notes=str(data.get('notes','')).strip()
                        if len(short_form)>4: return self.send_json({'error':'Short form must be 4 characters or fewer.'},400)
                        position=db.execute('SELECT coalesce(max(sort_order),-1)+1 AS next FROM characters WHERE drama_id=?',(did,)).fetchone()['next']
                        db.execute('INSERT INTO characters(drama_id,short_form,name,notes,sort_order) VALUES(?,?,?,?,?)',(did,short_form,value,notes,position))
                    db.execute("UPDATE dramas SET updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(did,))
                    return self.send_json({'ok':True},201)
                if kind == 'moments':
                    text = str(data.get('text','')).strip(); emoji = str(data.get('emoji','📖'))
                    rating = int(data.get('rating',0))
                    if not text: return self.send_json({'error':'Please add a note.'},400)
                    if emoji not in ('📖','💋','🦋','💍','🖐️','⚔️','💀','💧','📄') or rating not in range(0,5): return self.send_json({'error':'Invalid selection.'},400)
                    current = db.execute('SELECT episode FROM dramas WHERE id=?',(did,)).fetchone()['episode']
                    ep = None if emoji == '📄' else max(0, int(data.get('episode',current)))
                    db.execute('INSERT INTO moments(drama_id,episode,emoji,rating,text) VALUES(?,?,?,?,?)',(did,ep,emoji,rating,text))
                    db.execute("UPDATE dramas SET updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(did,))
                    return self.send_json({'ok':True},201)
        return self.send_json({'error':'Not found'},404)

    def do_DELETE(self):
        parts = urlparse(self.path).path.strip('/').split('/')
        if parts[:2] != ['api','dramas']: return self.send_json({'error':'Not found'},404)
        try: did = int(parts[2])
        except (ValueError, IndexError): return self.send_json({'error':'Not found'},404)
        with connect() as db:
            if len(parts) == 3:
                poster=db.execute('SELECT poster_file FROM dramas WHERE id=?',(did,)).fetchone()
                db.execute('DELETE FROM dramas WHERE id=?',(did,))
                if poster: remove_saved_poster(poster['poster_file'])
                return self.send_json({'ok':True})
            if len(parts) == 5:
                table = {'characters':'characters','plot_points':'plot_points','moments':'moments'}.get(parts[3])
                try: item_id = int(parts[4])
                except ValueError: return self.send_json({'error':'Not found'},404)
                if not table: return self.send_json({'error':'Not found'},404)
                db.execute(f'DELETE FROM {table} WHERE id=? AND drama_id=?',(item_id,did))
                db.execute("UPDATE dramas SET updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(did,))
                return self.send_json({'ok':True})
        return self.send_json({'error':'Not found'},404)

    def do_PUT(self):
        parts = urlparse(self.path).path.strip('/').split('/')
        if len(parts) == 3 and parts[:2] == ['api','dramas']:
            try: did = int(parts[2])
            except ValueError: return self.send_json({'error':'Not found'},404)
            data = self.body()
            title_value = None
            if 'title' in data:
                title_value = str(data.get('title','')).strip()
                if not title_value: return self.send_json({'error':'Please enter a show name.'},400)
            text_updates = {}
            for field, limit in (('genre',100),('cast_1',100),('cast_2',100),('cast_3',100),('cast_4',100)):
                if field in data:
                    value=str(data.get(field) or '').strip()
                    if len(value)>limit: return self.send_json({'error':f'{field.replace("_", " ").title()} must be {limit} characters or fewer.'},400)
                    text_updates[field]=value
            if 'poster_url' in data:
                poster_url=str(data.get('poster_url') or '').strip()
                if len(poster_url)>1000: return self.send_json({'error':'Poster image link must be 1000 characters or fewer.'},400)
                if poster_url:
                    parsed=urlparse(poster_url)
                    if parsed.scheme not in ('http','https') or not parsed.netloc: return self.send_json({'error':'Enter a valid http or https poster image link.'},400)
                text_updates['poster_url']=poster_url
                text_updates['poster_file']=''
            watch_dates = {}
            for field in ('watch_start_date','watch_end_date'):
                if field in data:
                    value=str(data.get(field) or '').strip()
                    if value:
                        try: value=date.fromisoformat(value).isoformat()
                        except ValueError: return self.send_json({'error':'Enter dates in YYYY-MM-DD format.'},400)
                    else:
                        value=None
                    watch_dates[field]=value
            episode_count_value = None
            if 'episode_count' in data:
                episode_count_value=data.get('episode_count')
                if episode_count_value is not None:
                    try: episode_count_value=int(episode_count_value)
                    except (TypeError,ValueError): return self.send_json({'error':'Episode count must be between 1 and 9999.'},400)
                    if episode_count_value not in range(1,10000): return self.send_json({'error':'Episode count must be between 1 and 9999.'},400)
            score_value = None
            if 'show_score' in data:
                score_value = data.get('show_score')
                if score_value is not None:
                    try: score_value = round(float(score_value),1)
                    except (TypeError,ValueError): return self.send_json({'error':'Choose a rating from 0.0 to 10.0.'},400)
                    if not 0 <= score_value <= 10: return self.send_json({'error':'Choose a rating from 0.0 to 10.0.'},400)
            category_values = {}
            for column in ('romance_rating','action_rating','character_rating','plot_rating','visuals_rating'):
                if column in data:
                    try: value = int(data[column])
                    except (TypeError,ValueError): return self.send_json({'error':'Category ratings must be from 0 to 4.'},400)
                    if value not in range(0,5): return self.send_json({'error':'Category ratings must be from 0 to 4.'},400)
                    category_values[column] = value
            with connect() as db:
                if not db.execute('SELECT id FROM dramas WHERE id=?',(did,)).fetchone():
                    return self.send_json({'error':'Drama not found.'},404)
                previous_poster=db.execute('SELECT poster_file FROM dramas WHERE id=?',(did,)).fetchone()['poster_file'] if 'poster_url' in data else ''
                if title_value is not None:
                    db.execute("UPDATE dramas SET title=?,updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(title_value,did))
                if text_updates:
                    assignments=','.join(f'{field}=?' for field in text_updates)
                    db.execute(f"UPDATE dramas SET {assignments},updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(*text_updates.values(),did))
                    if 'poster_url' in data: remove_saved_poster(previous_poster)
                if 'episode_count' in data:
                    db.execute("UPDATE dramas SET episode_count=?,updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(episode_count_value,did))
                if watch_dates:
                    assignments=','.join(f'{field}=?' for field in watch_dates)
                    db.execute(f"UPDATE dramas SET {assignments},updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(*watch_dates.values(),did))
                if 'show_score' in data:
                    db.execute("UPDATE dramas SET show_score=?,completed_at=CASE WHEN ? IS NULL THEN NULL ELSE coalesce(completed_at,strftime('%Y-%m-%d %H:%M:%f','now')) END,watch_end_date=CASE WHEN ? IS NULL THEN NULL ELSE coalesce(watch_end_date,date('now')) END,updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(score_value,score_value,score_value,did))
                if category_values:
                    assignments=','.join(f'{column}=?' for column in category_values)
                    db.execute(f"UPDATE dramas SET {assignments},updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(*category_values.values(),did))
                return self.send_json(rowdict(db.execute('SELECT * FROM dramas WHERE id=?',(did,)).fetchone()))
        if len(parts) != 5 or parts[:2] != ['api','dramas'] or parts[3] not in ('characters','moments'):
            return self.send_json({'error':'Not found'},404)
        try: did, item_id = int(parts[2]), int(parts[4])
        except ValueError: return self.send_json({'error':'Not found'},404)
        data = self.body()
        with connect() as db:
            if parts[3] == 'characters':
                name = str(data.get('name','')).strip()
                if not name: return self.send_json({'error':'Please enter a character name.'},400)
                short_form=str(data.get('short_form','')).strip()
                notes=str(data.get('notes','')).strip()
                if len(short_form)>4: return self.send_json({'error':'Short form must be 4 characters or fewer.'},400)
                result = db.execute('UPDATE characters SET short_form=?,name=?,notes=? WHERE id=? AND drama_id=?',(short_form,name,notes,item_id,did))
                if not result.rowcount: return self.send_json({'error':'Character not found.'},404)
            else:
                moment = db.execute('SELECT * FROM moments WHERE id=? AND drama_id=?',(item_id,did)).fetchone()
                if not moment: return self.send_json({'error':'Moment not found.'},404)
                text = str(data.get('text',moment['text'])).strip()
                emoji = str(data.get('emoji',moment['emoji']))
                try: rating = int(data.get('rating',moment['rating']))
                except (TypeError,ValueError): return self.send_json({'error':'Hit value must be from 0 to 4.'},400)
                allowed = ('📖','💋','🦋','💍','🖐️','⚔️','💀','💧','📄')
                if not text: return self.send_json({'error':'Please add a note.'},400)
                if emoji not in allowed or rating not in range(0,5): return self.send_json({'error':'Invalid moment selection.'},400)
                if emoji == '📄':
                    episode = None
                else:
                    current = db.execute('SELECT episode FROM dramas WHERE id=?',(did,)).fetchone()['episode']
                    try: episode = max(0,int(data.get('episode',moment['episode'] if moment['episode'] is not None else current)))
                    except (TypeError,ValueError): return self.send_json({'error':'Episode must be a number.'},400)
                db.execute('UPDATE moments SET text=?,emoji=?,rating=?,episode=? WHERE id=? AND drama_id=?',(text,emoji,rating,episode,item_id,did))
            db.execute("UPDATE dramas SET updated_at=strftime('%Y-%m-%d %H:%M:%f','now') WHERE id=?",(did,))
        return self.send_json({'ok':True})

    def log_message(self, fmt, *args):
        print('%s - %s' % (self.address_string(), fmt % args))


if __name__ == '__main__':
    init_db()
    route = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route.connect(('8.8.8.8', 80))
        lan_ip = route.getsockname()[0]
    except OSError:
        lan_ip = 'your-computer-ip'
    finally:
        route.close()
    print(f'C-drama tracker running at http://127.0.0.1:{PORT}')
    print(f'On your home network, open http://{lan_ip}:{PORT}.')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()

