from functools import wraps
from flask import session, redirect
from pytz import timezone
from datetime import datetime

def now_sp_str():
    return datetime.now(timezone('America/Sao_Paulo')).strftime('%Y-%m-%d %H:%M:%S')

def require_login(f):
    @wraps(f)
    def w(*a, **k):
        if 'usuario' not in session:
            return redirect('/login')
        return f(*a, **k)
    return w

def require_roles(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            papel = (session.get('papel') or '').upper()
            if papel not in roles:
                return ('Acesso negado', 403)
            return f(*a, **k)
        return w
    return deco
