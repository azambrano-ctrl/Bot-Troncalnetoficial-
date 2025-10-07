# --- SISTEMA DE MEMORIA PERSISTENTE ---
SESSION_FILE = "session_data.json"

def guardar_estado(user_id, state_data):
    try:
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                sessions = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            sessions = {}
        sessions[user_id] = state_data
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(sessions, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error guardando estado: {e}")
# Al inicio de bot/state_manager.py
import json
import os

# Define la constante del archivo de sesión aquí
SESSION_FILE = "session_data.json"

def cargar_estado(user_id):
    try:
        with open(SESSION_FILE, 'r', encoding='utf-8') as f:
            sessions = json.load(f)
        return sessions.get(user_id, {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    except Exception as e:
        print(f"Error cargando estado: {e}")
        return {}

def borrar_estado(user_id):
    try:
        try:
            with open(SESSION_FILE, 'r', encoding='utf-8') as f:
                sessions = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return
        if user_id in sessions:
            state = sessions[user_id]
            if 'temp_filepath' in state and state['temp_filepath'] and os.path.exists(state['temp_filepath']):
                try:
                    os.remove(state['temp_filepath'])
                    print(f"Archivo temporal eliminado: {state['temp_filepath']}")
                except Exception as e:
                    print(f"Error eliminando archivo temporal: {e}")
            del sessions[user_id]
        with open(SESSION_FILE, 'w', encoding='utf-8') as f:
            json.dump(sessions, f, ensure_ascii=False, indent=4)
    except Exception as e:
        print(f"Error borrando estado: {e}")
