# Al inicio de bot/client_service.py
import unicodedata
import re
import random

# --- FUNCIONES DE VALIDACIÓN Y EXTRACCIÓN ---
def parse_client_line(line):
    try:
        parts = line.strip().split(';', 1)
        if len(parts) == 2:
            identificacion = parts[0].strip()
            nombre = parts[1].strip()
            if identificacion and nombre:
                return identificacion, nombre
        return None, None
    except Exception:
        return None, None

def get_client_phrases():
    phrases = []
    try:
        with open('base_clientes.txt', 'r', encoding='utf-8') as f:
            for line in f:
                _, nombre = parse_client_line(line)
                if nombre:
                    if len(nombre) <= 100:
                        phrases.append(nombre)
                    name_parts = nombre.split()
                    if len(name_parts) > 1:
                        if len(name_parts[0]) <= 100:
                            phrases.append(name_parts[0])
                        if len(name_parts[-1]) <= 100:
                            phrases.append(name_parts[-1])
        
        unique_phrases = list(set(phrases))
        
        random.shuffle(unique_phrases)
        
        final_phrases = []
        total_chars = 0
        
        for phrase in unique_phrases:
            if len(final_phrases) < 5000 and (total_chars + len(phrase)) < 100000:
                final_phrases.append(phrase)
                total_chars += len(phrase)
            else:
                break
        
        print(f"Enviando {len(final_phrases)} frases ({total_chars} caracteres) a la API de Speech.")
        return final_phrases

    except FileNotFoundError:
        print("ADVERTENCIA: No se encontró 'base_clientes.txt' para las pistas de audio.")
        return []
    except Exception as e:
        print(f"Error leyendo frases de clientes: {e}")
        return []

def buscar_nombre_por_id(identificacion):
    if not identificacion: return None
    try:
        with open('base_clientes.txt', 'r', encoding='utf-8') as f:
            for line in f:
                id_base, nombre_base = parse_client_line(line)
                if id_base and id_base == identificacion:
                    return nombre_base
    except FileNotFoundError:
        print("ADVERTENCIA: No se encontró 'base_clientes.txt'.")
    except Exception as e:
        print(f"Error leyendo base de clientes: {e}")
    return None

def buscar_id_por_nombre(nombre_usuario):
    if not nombre_usuario or len(nombre_usuario.strip()) < 4:
        return []

    def limpiar_texto(texto):
        nfkd_form = unicodedata.normalize('NFKD', texto.lower())
        texto_limpio = "".join([c for c in nfkd_form if not unicodedata.combining(c)])
        return re.sub(r'[^a-z0-9\s]', '', texto_limpio)

    try:
        query = limpiar_texto(nombre_usuario)
        query_words = set(query.split())

        matches = []
        with open('base_clientes.txt', 'r', encoding='utf-8') as f:
            for line in f:
                id_base, nombre_base = parse_client_line(line)
                if not (id_base and nombre_base):
                    continue
                
                nombre_limpio = limpiar_texto(nombre_base)
                
                score = 0
                nombre_base_words = set(nombre_limpio.split())
                palabras_comunes = query_words.intersection(nombre_base_words)
                
                if not palabras_comunes:
                    continue

                if query_words.issubset(nombre_base_words):
                    score += 100 * len(query_words)
                else:
                    score += 20 * len(palabras_comunes)
                
                for q_word in query_words:
                    if len(q_word) > 2:
                        for db_word in nombre_base_words:
                            if q_word in db_word:
                                score += 5
                                if db_word.startswith(q_word):
                                    score += 10
                
                if score > 10:
                    matches.append(((id_base, nombre_base), score))

        matches.sort(key=lambda x: x[1], reverse=True)
        return matches[:5]

    except FileNotFoundError:
        print("ADVERTENCIA: No se encontró 'base_clientes.txt'.")
        return []
    except Exception as e:
        print(f"Error buscando por nombre: {e}")
        return []