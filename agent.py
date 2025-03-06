import os
import subprocess
import psutil
import platform
import time
import threading
import sys
import shutil
import requests  # pip install requests
import hashlib
from flask import Flask, jsonify, abort, request

app = Flask(__name__)

# ------------------------------------------------------------------------------
#                               Конфигурация
# ------------------------------------------------------------------------------
users = [
    {"name": "Alice",   "ip": "172.19.0.1"},
    {"name": "Bob",     "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

# Это ОДНА ссылка, где лежит либо agent.exe, либо agent.py
# Если расширение .exe — будем скачивать и запускать как exe
# Если .py — будем компилировать, если агент сам .exe; иначе «скриптовое» обновление
UPDATE_URL = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/agent.py"

VERISONAPP = '1.0.1'
UPDATE_CHECK_INTERVAL = 60  # секунды

@app.route('/version', methods=['GET'])
def version_get():
    return jsonify({"Version": VERISONAPP})

# ------------------------------------------------------------------------------
#                        Вспомогательные функции
# ------------------------------------------------------------------------------
def is_windows():
    return platform.system().lower() == "windows"

def is_compiled():
    """
    Проверяем, запущен ли агент как скомпилированный бинарник (PyInstaller).
    Если sys.argv[0] заканчивается на .exe — значит, скомпилирован.
    """
    return sys.argv[0].lower().endswith(".exe")

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

def compute_hash(data):
    """Считаем SHA256 для переданных байт."""
    hash_func = hashlib.sha256()
    hash_func.update(data)
    return hash_func.hexdigest()

def normalize_code(data):
    """
    Для .py: убираем \r\n и триммируем строки,
    чтобы мелкие правки перевода строк не меняли хэш.
    """
    try:
        text = data.decode('utf-8')
        text = text.replace('\r\n', '\n')
        text = text.strip()
        lines = [line.rstrip() for line in text.split('\n')]
        normalized = '\n'.join(lines)
        return normalized.encode('utf-8')
    except:
        return data

def current_file_hash():
    """Хэш текущего запущенного файла (agent.exe или agent.py)."""
    current_file = os.path.abspath(sys.argv[0])
    with open(current_file, "rb") as f:
        return compute_hash(f.read())

def code_has_changed(new_data, is_py=False):
    """
    Сравниваем хэш текущего файла (exe/py) с `new_data`.
    Если is_py=True, нормализуем скачанный код и текущий (если вдруг .py).
    Если агент .exe, обычно нормализовать нечего, но оставим единый подход.
    """
    try:
        current_file = os.path.abspath(sys.argv[0])
        with open(current_file, "rb") as f:
            current_data = f.read()

        if is_py:
            # Нормализуем
            normalized_current = normalize_code(current_data)
            normalized_new = normalize_code(new_data)
        else:
            # Просто сравниваем как бинарники
            normalized_current = current_data
            normalized_new = new_data

        old_hash = compute_hash(normalized_current)
        new_hash = compute_hash(normalized_new)
        changed = (old_hash != new_hash)
        return changed, old_hash, new_hash

    except FileNotFoundError:
        print("[INFO] Текущий файл агента не найден — считаем, что код изменился.")
        return True, None, None
    except Exception as e:
        print("[ERROR] Ошибка при вычислении хэша:", e)
        return True, None, None

# ------------------------------------------------------------------------------
#                     Основная проверка обновлений
# ------------------------------------------------------------------------------
def check_for_updates():
    """
    Скачивает данные по UPDATE_URL.  
    Определяет: .exe это или .py (по расширению из UPDATE_URL).  
    Сравнивает с текущим файлом.  
    Возвращает dict:
      {
        "update_available": True/False,
        "update_url": UPDATE_URL,
        "file_type": "exe" или "py",
        "old_hash": ...,
        "new_hash": ...
      }
    """
    try:
        # Определяем по ссылке, что мы скачиваем
        lower_url = UPDATE_URL.lower()
        if lower_url.endswith(".exe"):
            file_type = "exe"
        elif lower_url.endswith(".py"):
            file_type = "py"
        else:
            # Если не .exe и не .py — пусть будет "exe" по умолчанию,
            # или можно выдать ошибку.
            file_type = "exe"

        resp = requests.get(UPDATE_URL, timeout=30)
        if resp.status_code != 200:
            print("[ERROR] Ошибка скачивания обновления, статус:", resp.status_code)
            return {"update_available": False, "error": f"HTTP {resp.status_code}"}

        new_data = resp.content

        # Сравниваем
        if file_type == "py":
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        else:
            changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)

        if changed:
            return {
                "update_available": True,
                "file_type": file_type,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "update_url": UPDATE_URL
            }
        else:
            return {"update_available": False}

    except Exception as e:
        print("[ERROR] Ошибка при проверке обновлений:", str(e))
        return {"update_available": False, "error": str(e)}

# ------------------------------------------------------------------------------
#        1) Обновление, когда скачиваем готовый .exe (без компиляции)
# ------------------------------------------------------------------------------
def perform_update_exe_direct(update_url):
    """
    Если в репо лежит .exe (и agent сам .exe):
    1) Скачиваем,
    2) Сравниваем хэш,
    3) Сохраняем как agent_new_XXXX.exe,
    4) Запускаем,
    5) Убиваем старый процесс.
    """
    print("[INFO] Обновление: скачиваем готовый .exe по URL:", update_url)
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            msg = f"Ошибка скачивания .exe, статус: {resp.status_code}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)
        if not changed:
            msg = "Файл .exe не изменился. Обновление не требуется."
            print("[INFO]", msg)
            return {"success": False, "message": msg}

        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        timestamp = time.strftime("%Y%m%d%H%M%S")
        new_exe_path = os.path.join(current_dir, f"agent_new_{timestamp}.exe")

        with open(new_exe_path, "wb") as f:
            f.write(new_data)
        print("[INFO] Сохранён новый exe:", new_exe_path)

        # Запускаем
        print("[INFO] Запускаем новый exe-процесс...")
        try:
            new_proc = subprocess.Popen(
                [new_exe_path],
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                close_fds=True
            )
            print("[INFO] Новый процесс запущен, PID:", new_proc.pid)
        except Exception as e:
            msg = f"Не удалось запустить скачанный exe: {e}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}

        # (Опционально) ждём пару секунд, проверяем порт
        time.sleep(5)
        flask_running = False
        for proc in psutil.process_iter(["pid", "connections"]):
            try:
                for conn in proc.info.get("connections", []):
                    if conn.laddr and conn.laddr.port == 5000:
                        flask_running = True
                        print(f"[INFO] Новый Flask на порту 5000 (PID={proc.info['pid']})")
                        break
            except:
                pass

        if not flask_running:
            print("[WARNING] Новый агент не виден на порту 5000. Проверяйте логи.")

        # Завершаем старый
        print("[INFO] Завершаем текущий процесс (старый агент).")
        os._exit(0)

    except Exception as e:
        msg = f"Исключение при обновлении .exe: {e}"
        print("[ERROR]", msg)
        return {"success": False, "message": msg}

# ------------------------------------------------------------------------------
#        2) Обновление, когда скачиваем .py и ПЕРЕКОМПИЛИРОВЫВАЕМ в exe
#           (работает только если текущий агент - exe и есть PyInstaller)
# ------------------------------------------------------------------------------
def perform_update_compile_py(update_url):
    """
    Если репо содержит .py, а текущий агент - .exe, мы:
      1) Скачиваем .py
      2) Компилируем его в новый exe (PyInstaller должен быть установлен)
      3) Запускаем
      4) Убиваем старый процесс
    """
    print("[INFO] Обновление (exe-режим): скачиваем .py и компилируем через PyInstaller.")
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            msg = f"Ошибка скачивания .py, статус: {resp.status_code}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            msg = "Исходный .py не изменился. Обновление не требуется."
            print("[INFO]", msg)
            return {"success": False, "message": msg}

        current_dir = os.path.dirname(os.path.abspath(sys.argv[0]))
        timestamp = time.strftime("%Y%m%d%H%M%S")
        new_py_path = os.path.join(current_dir, f"agent_new_{timestamp}.py")

        with open(new_py_path, "wb") as f:
            f.write(new_data)
        print("[INFO] Сохранён новый .py:", new_py_path)

        # Компилируем через PyInstaller
        compile_cmd = [
            sys.executable, "-m", "PyInstaller",
            "--onefile",
            "--noconsole",
            new_py_path
        ]
        print("[INFO] Запускаем PyInstaller:", compile_cmd)
        result = subprocess.run(compile_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if result.returncode != 0:
            print("[ERROR] PyInstaller завершился с ошибкой:")
            print(result.stdout)
            return {"success": False, "message": "PyInstaller error. Check logs."}

        # Ищем результат в dist/
        base_name = os.path.splitext(os.path.basename(new_py_path))[0] + ".exe"
        dist_exe = os.path.join(current_dir, "dist", base_name)
        if not os.path.isfile(dist_exe):
            msg = f"Не найден скомпилированный exe: {dist_exe}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}

        # Копируем в удобное имя
        new_exe_path = os.path.join(current_dir, f"agent_new_{timestamp}.exe")
        shutil.copyfile(dist_exe, new_exe_path)
        print("[INFO] Готов новый exe:", new_exe_path)

        # Запускаем
        print("[INFO] Запуск нового exe:")
        new_proc = subprocess.Popen(
            [new_exe_path],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )
        print("[INFO] Новый процесс PID:", new_proc.pid)

        time.sleep(5)
        flask_running = False
        for proc in psutil.process_iter(["pid", "connections"]):
            try:
                for conn in proc.info.get("connections", []):
                    if conn.laddr and conn.laddr.port == 5000:
                        flask_running = True
                        print(f"[INFO] Новый Flask на 5000 (PID={proc.info['pid']})")
                        break
            except:
                pass
        if not flask_running:
            print("[WARNING] Новый агент не виден на порту 5000.")

        print("[INFO] Завершаем старый процесс.")
        os._exit(0)

    except Exception as e:
        msg = f"Исключение при обновлении (.py -> .exe): {e}"
        print("[ERROR]", msg)
        return {"success": False, "message": msg}

# ------------------------------------------------------------------------------
#        3) Обновление «скриптовое» (если сам агент .py)
# ------------------------------------------------------------------------------
def perform_update_script_py(update_url):
    """
    Если агент запущен как .py, и в репо тоже лежит .py:
    1) Скачиваем
    2) Сравниваем
    3) Сохраняем, делаем backup
    4) Запускаем новый .py
    5) Убиваем старый процесс
    """
    print("[INFO] Обновление (скриптовый режим) через .py:", update_url)
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            msg = f"Ошибка скачивания .py, статус: {resp.status_code}"
            print("[ERROR]", msg)
            return {"success": False, "message": msg}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            msg = "Новый .py не отличается. Обновление не требуется."
            print("[INFO]", msg)
            return {"success": False, "message": msg}

        current_file = os.path.abspath(sys.argv[0])
        current_dir = os.path.dirname(current_file)
        timestamp = time.strftime("%Y%m%d%H%M%S")

        new_file_name = os.path.join(current_dir, f"agent_new_{timestamp}.py")
        backup_file = os.path.join(current_dir, f"agent_backup_{timestamp}.py")

        with open(new_file_name, "wb") as f:
            f.write(new_data)
        print("[INFO] Скачанный скрипт сохранён в:", new_file_name)

        # Резервная копия старого
        shutil.move(current_file, backup_file)
        print("[INFO] Старый файл перемещён в:", backup_file)

        # Запуск нового
        print("[INFO] Запускаем новый скрипт:")
        new_proc = subprocess.Popen(
            [sys.executable, new_file_name],
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            close_fds=True
        )
        print("[INFO] Новый процесс PID:", new_proc.pid)

        time.sleep(5)
        flask_running = False
        for proc in psutil.process_iter(["pid", "connections"]):
            try:
                for conn in proc.info.get("connections", []):
                    if conn.laddr and conn.laddr.port == 5000:
                        flask_running = True
                        print(f"[INFO] Новый Flask на порту 5000 (PID={proc.info['pid']})")
                        break
            except:
                pass

        if not flask_running:
            print("[WARNING] Новый скрипт не поднял Flask на 5000. Проверяйте логи.")

        print("[INFO] Завершаем старый процесс.")
        os._exit(0)

    except Exception as e:
        msg = f"Исключение при обновлении скриптом: {e}"
        print("[ERROR]", msg)
        return {"success": False, "message": msg}

# ------------------------------------------------------------------------------
#        Единая логика: вызывается при /update или фоновой проверке
# ------------------------------------------------------------------------------
def do_update_if_available():
    info = check_for_updates()
    if not info.get("update_available"):
        return {"success": False, "message": "Обновлений не обнаружено"}

    file_type = info.get("file_type", "exe")
    if file_type == "exe":
        # Репо содержит .exe
        if is_compiled():
            # Текущий агент .exe => Просто скачиваем готовый exe и запускаем
            return perform_update_exe_direct(info["update_url"])
        else:
            # Текущий агент - скрипт .py, но в репо лежит .exe
            # Теоретически можно «запустить .exe», но обычно это нетипично:
            # Можно просто скачать и запускать — или игнорировать.
            print("[WARNING] Агент в виде .py, но обновление — .exe. Запустим .exe напрямую.")
            return perform_update_exe_direct(info["update_url"])

    else:
        # file_type == "py"
        if is_compiled():
            # Текущий агент - exe. Значит, качаем .py => компилируем => запускаем
            return perform_update_compile_py(info["update_url"])
        else:
            # Текущий агент - скрипт. Обычный «скриптовый» сценарий
            return perform_update_script_py(info["update_url"])


# ------------------------------------------------------------------------------
#                         Flask: основная логика
# ------------------------------------------------------------------------------
@app.route('/update', methods=['GET', 'POST'])
def update_endpoint():
    """
    Ручной вызов обновления. Если есть новая версия (.exe или .py),
    агент скачает, подготовит и перезапустится.
    """
    result = do_update_if_available()
    if result.get("success"):
        return jsonify(result), 200
    else:
        # "success": False, "message": ...
        return jsonify(result), 200

@app.route('/users', methods=['GET'])
def list_users():
    return jsonify({"users": users})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({
        "user": user,
        "metrics": get_metrics(),
        "directories": get_user_directories()
    })

@app.route('/connect/<username>/<metric_name>', methods=['GET'])
def connect_to_user_metric(username, metric_name):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    metrics_data = get_metrics()
    if metric_name in metrics_data:
        return jsonify({metric_name: metrics_data[metric_name]})
    else:
        abort(404, description="Metric not found")

@app.route('/connect/<username>/directories', methods=['GET'])
def connect_to_user_directories(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"directories": get_user_directories()})

@app.route('/metrics', methods=['GET'])
def metrics():
    return jsonify(get_metrics())

def get_services():
    services = []
    if is_windows():
        for service in psutil.win_service_iter():
            try:
                services.append({
                    "name": service.name(),
                    "status": service.status(),
                    "display_name": service.display_name()
                })
            except Exception:
                pass
    else:
        result = subprocess.run(
            ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"],
            stdout=subprocess.PIPE,
            text=True
        )
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) > 1:
                services.append({"name": parts[0], "status": parts[2]})
    return services

@app.route('/services', methods=['GET'])
def list_services():
    return jsonify({"services": get_services()})

@app.route('/connect/<username>/services', methods=['GET'])
def connect_to_user_services(username):
    user = next((u for u in users if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"user": user, "services": get_services()})

# ------------------------------------------------------------------------------
#                           Метрики и сервисы
# ------------------------------------------------------------------------------
def get_metrics():
    cpu_usage = psutil.cpu_percent(interval=1)
    memory_info = psutil.virtual_memory()
    disk_info = psutil.disk_usage('/')
    processes = []
    for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
        processes.append({
            "pid": proc.info['pid'],
            "name": proc.info['name'],
            "cpu_usage": f"{proc.info['cpu_percent']:.1f}%",
            "memory_usage": convert_bytes(proc.info['memory_info'].rss),
        })
    return {
        "cpu": {
            "usage": f"{cpu_usage}%",
            "description": "Текущая загрузка CPU"
        },
        "memory": {
            "total": convert_bytes(memory_info.total),
            "used": convert_bytes(memory_info.used),
            "free": convert_bytes(memory_info.available),
            "percent": f"{memory_info.percent}%",
            "description": "Использование оперативной памяти"
        },
        "disk": {
            "total": convert_bytes(disk_info.total),
            "used": convert_bytes(disk_info.used),
            "free": convert_bytes(disk_info.free),
            "percent": f"{disk_info.percent}%",
            "description": "Использование диска"
        },
        "processes": processes,
        "last_update": time.strftime('%Y-%m-%d %H:%M:%S'),
        "system_info": {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.architecture()[0],
            "hostname": platform.node()
        }
    }

def get_user_directories():
    path = "C:/Users" if is_windows() else "/home"
    try:
        return [d for d in os.listdir(path) if os.path.isdir(os.path.join(path, d))]
    except Exception as e:
        print("[WARNING] Ошибка при получении списка директорий:", e)
        return []

# ------------------------------------------------------------------------------
#                         Фоновая проверка обновлений
# ------------------------------------------------------------------------------
def background_update_checker():
    while True:
        update_info = check_for_updates()
        if update_info.get("update_available"):
            print("[INFO] Фоновая проверка: обнаружено обновление.")
            # Пытаемся обновиться. Если успех — текущий процесс завершается.
            result = do_update_if_available()
            if result.get("success"):
                print("[INFO] Обновились фоново. Ожидаем перезапуск...")
                break
            else:
                print("[ERROR] Фоновое обновление не удалось:", result.get("message"))
        time.sleep(UPDATE_CHECK_INTERVAL)

# ------------------------------------------------------------------------------
#                               Запуск
# ------------------------------------------------------------------------------
if __name__ == '__main__':
    print("[INFO] Запущена версия агента:", VERISONAPP, " PID:", os.getpid())
    threading.Thread(target=background_update_checker, daemon=True).start()
    app.run(host='0.0.0.0', port=5000, use_reloader=False)
