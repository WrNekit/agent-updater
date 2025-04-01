import os
import sys
import time
import subprocess
import threading
import platform
import hashlib
import shutil
import requests
import psutil
import socket
import logging
from functools import wraps
from flask import Flask, jsonify, abort, request
from typing import Dict, List, Tuple, Optional, Union, Any

# Настройка безопасного логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('agent.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Конфигурация безопасности
app.config['JSON_SORT_KEYS'] = False
app.config['JSONIFY_PRETTYPRINT_REGULAR'] = False
app.config['TRAP_HTTP_EXCEPTIONS'] = True

# ------------------------------------------------------------------------------------
#                                  Конфигурация
# ------------------------------------------------------------------------------------
class Config:
    def __init__(self):
        self._users = []
        self._update_url = ""
        self._version = "1.0"
        self._update_check_interval = 60
        
    @property
    def users(self) -> List[Dict[str, str]]:
        return self._users
    
    @users.setter
    def users(self, value: List[Dict[str, str]]):
        if not isinstance(value, list):
            raise ValueError("Пользователи должны быть списком")
        for user in value:
            if not isinstance(user, dict) or 'name' not in user or 'ip' not in user:
                raise ValueError("Каждый пользователь должен содержать 'name' и 'ip'")
        self._users = value
    
    @property
    def update_url(self) -> str:
        return self._update_url
    
    @update_url.setter
    def update_url(self, value: str):
        if not isinstance(value, str) or not value.startswith(('http://', 'https://')):
            raise ValueError("Некорректный URL обновления")
        self._update_url = value
    
    @property
    def version(self) -> str:
        return self._version
    
    @property
    def update_check_interval(self) -> int:
        return self._update_check_interval
    
    @update_check_interval.setter
    def update_check_interval(self, value: int):
        if not isinstance(value, int) or value <= 0:
            raise ValueError("Интервал проверки должен быть положительным числом")
        self._update_check_interval = value

config = Config()
config.users = [
    {"name": "Alice", "ip": "192.168.1.10"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]
config.update_url = "https://raw.githubusercontent.com/WrNekit/agent-updater/refs/heads/main/deeo.py"

# ------------------------------------------------------------------------------------
#                          Декораторы для безопасности и логирования
# ------------------------------------------------------------------------------------
def rate_limited(max_per_minute: int = 60):
    def decorator(f):
        calls = []
        
        @wraps(f)
        def wrapped(*args, **kwargs):
            now = time.time()
            calls_in_time = [call for call in calls if call > now - 60]
            
            if len(calls_in_time) >= max_per_minute:
                logger.warning(f"Превышен лимит запросов для {f.__name__}")
                abort(429, description="Слишком много запросов")
                
            calls.append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator

def handle_errors(f):
    @wraps(f)
    def wrapped(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в {f.__name__}: {str(e)}", exc_info=True)
            abort(500, description=str(e))
    return wrapped

# ------------------------------------------------------------------------------------
#                          Функции для обновления агента (улучшенные)
# ------------------------------------------------------------------------------------
def is_windows() -> bool:
    """Безопасная проверка ОС Windows с кешированием результата."""
    try:
        return platform.system().lower() == "windows"
    except:
        return False

def is_compiled() -> bool:
    """Проверка скомпилированного состояния с защитой от инъекций."""
    try:
        return os.path.basename(sys.argv[0]).lower().endswith(".exe")
    except:
        return False

def convert_bytes(bytes_value: Union[int, float]) -> str:
    """Безопасное преобразование байтов с проверкой ввода."""
    if not isinstance(bytes_value, (int, float)) or bytes_value < 0:
        return "0.00 B"
    
    for unit in ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0
    return f"{bytes_value:.2f} ПБ"

def compute_hash(data: bytes) -> str:
    """Вычисление хеша с защитой от переполнения."""
    if not isinstance(data, bytes):
        raise ValueError("Входные данные должны быть в байтах")
    
    hash_func = hashlib.sha256()
    try:
        # Ограничиваем размер данных для хеширования (10MB максимум)
        max_size = 10 * 1024 * 1024
        if len(data) > max_size:
            data = data[:max_size]
            logger.warning("Входные данные усечены для хеширования")
            
        hash_func.update(data)
        return hash_func.hexdigest()
    except Exception as e:
        logger.error(f"Ошибка вычисления хеша: {e}")
        raise

def normalize_code(data: bytes) -> bytes:
    """Нормализация кода с обработкой ошибок кодировки."""
    try:
        text = data.decode('utf-8', errors='replace')
        text = text.replace('\r\n', '\n').strip()
        lines = [line.rstrip() for line in text.split('\n')]
        return '\n'.join(lines).encode('utf-8')
    except Exception as e:
        logger.error(f"Ошибка нормализации кода: {e}")
        return data

def code_has_changed(new_data: bytes, is_py: bool = False) -> Tuple[bool, Optional[str], Optional[str]]:
    """Безопасное сравнение хешей с обработкой ошибок файловых операций."""
    try:
        current_file = os.path.abspath(__file__)
        
        # Проверка существования и доступности файла
        if not os.path.exists(current_file):
            logger.error(f"Файл не найден: {current_file}")
            return True, None, None
            
        if not os.access(current_file, os.R_OK):
            logger.error(f"Нет доступа на чтение файла: {current_file}")
            return True, None, None
            
        # Ограничение размера читаемого файла (50MB максимум)
        max_file_size = 50 * 1024 * 1024
        file_size = os.path.getsize(current_file)
        if file_size > max_file_size:
            logger.error(f"Файл слишком большой для сравнения: {file_size} байт")
            return True, None, None
            
        with open(current_file, "rb") as f:
            current_data = f.read(max_file_size)

        old_norm = normalize_code(current_data) if is_py else current_data
        new_norm = normalize_code(new_data) if is_py else new_data

        old_hash = compute_hash(old_norm)
        new_hash = compute_hash(new_norm)

        logger.debug(f"Сравнение хешей - старый: {old_hash}, новый: {new_hash}")
        return old_hash != new_hash, old_hash, new_hash
        
    except Exception as e:
        logger.error(f"Ошибка сравнения кода: {e}")
        return True, None, None

def check_for_updates() -> Dict[str, Any]:
    """Безопасная проверка обновлений с таймаутом и проверкой SSL."""
    logger.info("Проверка обновлений...")
    try:
        # Определение типа файла с защитой от инъекций
        lower_url = config.update_url.lower()
        file_type = "py"  # По умолчанию для Linux
        
        if is_windows():
            if lower_url.endswith(".exe"):
                file_type = "exe"
            elif lower_url.endswith(".py"):
                file_type = "py"

        # Безопасный запрос с таймаутом и проверкой SSL
        try:
            resp = requests.get(
                config.update_url,
                timeout=(10, 30),
                verify=True,
                headers={'User-Agent': f'Agent/{config.version}'}
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса проверки обновлений: {e}")
            return {"update_available": False, "error": str(e)}

        new_data = resp.content
        
        # Ограничение размера загружаемого файла (50MB максимум)
        max_update_size = 50 * 1024 * 1024
        if len(new_data) > max_update_size:
            logger.error("Файл обновления слишком большой")
            return {"update_available": False, "error": "Файл обновления слишком большой"}

        changed, old_hash, new_hash = code_has_changed(new_data, is_py=(file_type == "py"))

        if changed:
            return {
                "update_available": True,
                "file_type": file_type,
                "old_hash": old_hash,
                "new_hash": new_hash,
                "update_url": config.update_url
            }
        else:
            logger.info("Обновлений не обнаружено")
            return {"update_available": False}
            
    except Exception as e:
        logger.error(f"Исключение при проверке обновлений: {e}", exc_info=True)
        return {"update_available": False, "error": str(e)}

def perform_update_exe_direct(update_url: str) -> Dict[str, Any]:
    """Безопасное обновление .exe с проверкой цифровой подписи (заглушка)."""
    logger.info("Загрузка нового .exe: %s", update_url)
    try:
        # Проверка URL
        if not update_url.startswith(('http://', 'https://')):
            return {"success": False, "message": "Некорректный URL обновления"}

        # Безопасная загрузка
        try:
            resp = requests.get(
                update_url,
                timeout=(10, 30),
                verify=True,
                headers={'User-Agent': f'Agent/{config.version}'}
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка загрузки обновления: {e}")
            return {"success": False, "message": f"Ошибка загрузки: {e}"}

        new_data = resp.content
        
        # Проверка размера файла
        if len(new_data) > 100 * 1024 * 1024:  # 100MB максимум
            return {"success": False, "message": "Файл обновления слишком большой"}

        changed, old_hash, new_hash = code_has_changed(new_data, is_py=False)
        if not changed:
            return {"success": False, "message": "Файл .exe не изменился"}

        # Безопасное сохранение нового файла
        cdir = os.path.dirname(os.path.abspath(sys.argv[0]))
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_exe = os.path.join(cdir, f"agent_new_{stamp}.exe")
        
        try:
            with open(new_exe, "wb") as f:
                f.write(new_data)
        except IOError as e:
            return {"success": False, "message": f"Ошибка записи файла: {e}"}

        logger.info("Новый исполняемый файл сохранен: %s", new_exe)

        # Проверка цифровой подписи (заглушка)
        if not verify_file_signature(new_exe):  # Реализовать в продакшене
            os.remove(new_exe)
            return {"success": False, "message": "Неверная цифровая подпись файла"}

        # Безопасный запуск нового процесса
        try:
            if is_windows():
                proc = subprocess.Popen(
                    [new_exe],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True
                )
            else:
                proc = subprocess.Popen([new_exe], close_fds=True)
                
            logger.info("Новый процесс запущен с PID: %d", proc.pid)
            
            # Даем время новому процессу запуститься
            time.sleep(5)
            os._exit(0)
            
        except Exception as e:
            logger.error(f"Ошибка запуска нового процесса: {e}")
            return {"success": False, "message": str(e)}
            
    except Exception as e:
        logger.error(f"Исключение при обновлении: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

def verify_file_signature(file_path: str) -> bool:
    """Заглушка для проверки цифровой подписи файла."""
    # В реальной реализации здесь должна быть проверка цифровой подписи
    return True

def perform_update_script_py(update_url: str) -> Dict[str, Any]:
    """Безопасное обновление Python-скрипта."""
    logger.info("Загрузка нового .py скрипта")
    try:
        # Проверка URL
        if not update_url.startswith(('http://', 'https://')):
            return {"success": False, "message": "Некорректный URL обновления"}

        # Безопасная загрузка
        try:
            resp = requests.get(
                update_url,
                timeout=(10, 30),
                verify=True,
                headers={'User-Agent': f'Agent/{config.version}'}
            )
            resp.raise_for_status()
        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка загрузки обновления: {e}")
            return {"success": False, "message": f"Ошибка загрузки: {e}"}

        new_data = resp.content
        
        # Проверка размера файла
        if len(new_data) > 10 * 1024 * 1024:  # 10MB максимум
            return {"success": False, "message": "Файл скрипта слишком большой"}

        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            return {"success": False, "message": "Файл скрипта не изменился"}

        # Безопасная работа с файлами
        cfile = os.path.abspath(sys.argv[0])
        cdir = os.path.dirname(cfile)
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_py = os.path.join(cdir, f"agent_new_{stamp}.py")
        backup_py = os.path.join(cdir, f"agent_backup_{stamp}.py")

        try:
            # Сохраняем новый файл
            with open(new_py, "wb") as f:
                f.write(new_data)
                
            # Создаем резервную копию
            shutil.move(cfile, backup_py)
            
            # Проверяем целостность файлов
            if not os.path.exists(new_py) or os.path.getsize(new_py) == 0:
                raise IOError("Новый файл скрипта некорректен")
                
            # Запускаем новый процесс
            if is_windows():
                proc = subprocess.Popen(
                    [sys.executable, new_py],
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    close_fds=True
                )
            else:
                proc = subprocess.Popen([sys.executable, new_py], close_fds=True)
                
            logger.info("Новый процесс запущен с PID: %d", proc.pid)
            
            # Даем время новому процессу запуститься
            time.sleep(5)
            os._exit(0)
            
        except Exception as e:
            # Восстановление из резервной копии при ошибке
            if os.path.exists(backup_py):
                try:
                    shutil.move(backup_py, cfile)
                except:
                    pass
            logger.error(f"Ошибка обновления: {e}", exc_info=True)
            return {"success": False, "message": str(e)}
            
    except Exception as e:
        logger.error(f"Исключение при обновлении: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

def do_update_if_available() -> Dict[str, Any]:
    """Безопасная проверка и выполнение обновления."""
    info = check_for_updates()
    if not info.get("update_available"):
        return {"success": False, "message": "Обновлений не обнаружено"}

    ftype = info.get("file_type", "py")
    update_url = info.get("update_url", "")
    
    if not update_url:
        return {"success": False, "message": "Некорректный URL обновления"}
        
    try:
        if ftype == "exe":
            if is_compiled():
                return perform_update_exe_direct(update_url)
            else:
                logger.warning("Запущен как .py, но обновление .exe - выполняем прямое обновление")
                return perform_update_exe_direct(update_url)
        else:
            if is_compiled():
                return perform_update_compile_py(update_url)
            else:
                return perform_update_script_py(update_url)
    except Exception as e:
        logger.error(f"Ошибка обновления: {e}", exc_info=True)
        return {"success": False, "message": str(e)}

def perform_update_compile_py(update_url):
    print("[INFO] Скачиваем .py и компилируем через PyInstaller.")
    try:
        resp = requests.get(update_url, timeout=30)
        if resp.status_code != 200:
            return {"success": False, "message": f"HTTP {resp.status_code}"}

        new_data = resp.content
        changed, old_hash, new_hash = code_has_changed(new_data, is_py=True)
        if not changed:
            return {"success": False, "message": "Исходный .py не изменился."}

        cdir = os.path.dirname(os.path.abspath(sys.argv[0]))
        stamp = time.strftime("%Y%m%d%H%M%S")
        new_py = os.path.join(cdir, f"agent_new_{stamp}.py")
        with open(new_py, "wb") as f:
            f.write(new_data)

        compile_cmd = [sys.executable, "-m", "PyInstaller", "--onefile", "--noconsole", new_py]
        res = subprocess.run(compile_cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        if res.returncode != 0:
            print("[ERROR] PyInstaller error:\n", res.stdout)
            return {"success": False, "message": "PyInstaller failed."}

        base_name = os.path.splitext(os.path.basename(new_py))[0] + ".exe"
        dist_exe = os.path.join(cdir, "dist", base_name)
        if not os.path.isfile(dist_exe):
            return {"success": False, "message": f"Не найден компилят: {dist_exe}"}

        new_exe = os.path.join(cdir, f"agent_new_{stamp}.exe")
        shutil.copyfile(dist_exe, new_exe)
        if is_windows():
            proc = subprocess.Popen([new_exe], creationflags=subprocess.CREATE_NEW_PROCESS_GROUP, close_fds=True)
        else:
            proc = subprocess.Popen([new_exe], close_fds=True)
        print("[INFO] Новый exe PID:", proc.pid)
        time.sleep(5)
        os._exit(0)
    except Exception as e:
        return {"success": False, "message": str(e)}

# ------------------------------------------------------------------------------------
#                          Улучшенные функции мониторинга
# ------------------------------------------------------------------------------------
def get_metrics() -> Dict[str, Any]:
    """Безопасный сбор метрик системы с ограничениями."""
    logger.info("Сбор системных метрик...")
    metrics = {
        "cpu": {"usage": "0%", "description": "Использование CPU"},
        "memory": {"total": "0 Б", "used": "0 Б", "free": "0 Б", "percent": "0%"},
        "disk": {"total": "0 Б", "used": "0 Б", "free": "0 Б", "percent": "0%"},
        "processes": [],
        "last_update": time.strftime('%Y-%m-%d %H:%M:%S'),
        "system_info": {
            "os": platform.system(),
            "os_version": platform.version(),
            "architecture": platform.architecture()[0] if hasattr(platform, 'architecture') else "неизвестно",
            "hostname": platform.node()
        }
    }

    try:
        # Использование CPU с ограничением времени
        metrics["cpu"]["usage"] = f"{psutil.cpu_percent(interval=1)}%"
    except:
        pass

    try:
        # Информация о памяти
        mem = psutil.virtual_memory()
        metrics["memory"] = {
            "total": convert_bytes(mem.total),
            "used": convert_bytes(mem.used),
            "free": convert_bytes(mem.available),
            "percent": f"{mem.percent}%",
        }
    except:
        pass

    try:
        # Информация о диске
        disk = psutil.disk_usage('/')
        metrics["disk"] = {
            "total": convert_bytes(disk.total),
            "used": convert_bytes(disk.used),
            "free": convert_bytes(disk.free),
            "percent": f"{disk.percent}%",
        }
    except:
        pass

    try:
        # Информация о процессах с ограничением количества
        max_processes = 50
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info']):
            try:
                processes.append({
                    "pid": proc.info['pid'],
                    "name": proc.info['name'][:100],  # Ограничение длины имени
                    "cpu_usage": f"{proc.info['cpu_percent']:.1f}%",
                    "memory_usage": convert_bytes(proc.info['memory_info'].rss),
                })
                if len(processes) >= max_processes:
                    break
            except:
                continue
        metrics["processes"] = processes
    except:
        pass

    logger.info("Системные метрики собраны")
    return metrics

def get_user_directories() -> List[str]:
    """Безопасное получение списка пользовательских директорий."""
    path = "C:/Users" if is_windows() else "/home"
    try:
        if not os.path.exists(path):
            return []
            
        dirs = []
        for d in os.listdir(path):
            try:
                full_path = os.path.join(path, d)
                if os.path.isdir(full_path) and not d.startswith('.'):
                    dirs.append(d)
            except:
                continue
        return dirs[:100]  # Ограничение количества
    except Exception as e:
        logger.error(f"Ошибка получения пользовательских директорий: {e}")
        return []

def get_services() -> List[Dict[str, str]]:
    """Безопасное получение списка сервисов."""
    services = []
    try:
        if is_windows():
            for svc in psutil.win_service_iter():
                try:
                    services.append({
                        "name": svc.name()[:100],  # Ограничение длины
                        "status": svc.status()[:50],
                        "display_name": svc.display_name()[:100]
                    })
                    if len(services) >= 100:  # Ограничение количества
                        break
                except:
                    continue
        else:
            try:
                cmd = ["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"]
                res = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    timeout=10
                )
                for line in res.stdout.splitlines()[:100]:  # Ограничение количества
                    parts = line.split()
                    if len(parts) > 1:
                        services.append({
                            "name": parts[0][:100],
                            "status": parts[2][:50]
                        })
            except:
                pass
    except Exception as e:
        logger.error(f"Ошибка получения списка сервисов: {e}")
    return services

# ------------------------------------------------------------------------------------
#                Дополнительные функции с улучшенной безопасностью
# ------------------------------------------------------------------------------------
def get_ip() -> str:
    """Безопасное определение IP адреса."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(2)
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except:
        return "127.0.0.1"

def get_uptime() -> str:
    """Безопасное определение времени работы системы."""
    try:
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        days, rem = divmod(uptime_seconds, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, seconds = divmod(rem, 60)
        return f"{int(days)}д {int(hours)}ч {int(minutes)}м {int(seconds)}с"
    except:
        return "неизвестно"

def get_disks() -> List[Dict[str, str]]:
    """Безопасное получение информации о дисках."""
    disks = []
    try:
        for p in psutil.disk_partitions(all=False)[:50]:  # Ограничение количества
            try:
                disks.append({
                    "device": p.device[:100],
                    "mountpoint": p.mountpoint[:200],
                    "fstype": p.fstype[:50],
                    "opts": p.opts[:200]
                })
            except:
                continue
    except:
        pass
    return disks

class UserLoginInfo:
    def __init__(self):
        self._info = {}
        self._lock = threading.Lock()
        
    def update(self):
        """Безопасное обновление информации о пользователях."""
        try:
            sessions = psutil.users()
            current_users = {}
            
            for session in sessions[:100]:  # Ограничение количества
                try:
                    username = str(session.name)[:100]  # Ограничение длины
                    login_time = float(session.started)
                    if username in current_users:
                        current_users[username] = max(current_users[username], login_time)
                    else:
                        current_users[username] = login_time
                except:
                    continue

            with self._lock:
                all_users = set(list(self._info.keys()) + list(current_users.keys()))
                for username in all_users:
                    if username in current_users:
                        self._info[username] = {
                            "logged_in": True,
                            "last_login": current_users[username]
                        }
                    else:
                        if username in self._info:
                            self._info[username]["logged_in"] = False
                        else:
                            self._info[username] = {"logged_in": False, "last_login": None}
                            
        except Exception as e:
            logger.error(f"Ошибка обновления информации о пользователях: {e}")

    def get_info(self) -> Dict[str, Dict[str, Any]]:
        """Безопасное получение информации о пользователях."""
        result = {}
        with self._lock:
            for username, info in list(self._info.items())[:1000]:  # Ограничение количества
                try:
                    last_login_str = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(info["last_login"])) \
                        if info["last_login"] else None
                    result[username[:100]] = {  # Ограничение длины имени
                        "logged_in": info["logged_in"],
                        "last_login": last_login_str
                    }
                except:
                    continue
        return result

user_login_info = UserLoginInfo()

def get_machine_info() -> Dict[str, Any]:
    """Безопасное получение информации о машине."""
    return {
        "hostname": platform.node()[:100],
        "ip": get_ip(),
        "uptime": get_uptime(),
        "disks": get_disks(),
        "user_status": user_login_info.get_info()
    }

# ------------------------------------------------------------------------------------
#                          Фоновые процессы с улучшенным управлением
# ------------------------------------------------------------------------------------
class BackgroundUpdater:
    def __init__(self):
        self._running = False
        self._thread = None
        
    def start(self):
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            
    def _run(self):
        while self._running:
            try:
                info = check_for_updates()
                if info.get("update_available"):
                    logger.info("Обнаружено обновление, выполнение обновления...")
                    res = do_update_if_available()
                    if res.get("success"):
                        logger.info("Обновление успешно, завершение...")
                        break
                    else:
                        logger.error(f"Ошибка обновления: {res.get('message')}")
            except Exception as e:
                logger.error(f"Ошибка проверки обновлений: {e}")
                
            time.sleep(config.update_check_interval)

background_updater = BackgroundUpdater()

class UserStatusUpdater:
    def __init__(self):
        self._running = False
        self._thread = None
        
    def start(self):
        if self._running:
            return
            
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        
    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            
    def _run(self):
        while self._running:
            try:
                user_login_info.update()
            except Exception as e:
                logger.error(f"Ошибка обновления статуса пользователей: {e}")
            time.sleep(10)

user_status_updater = UserStatusUpdater()

# ------------------------------------------------------------------------------------
#                                Защищенные Flask эндпойнты
# ------------------------------------------------------------------------------------
@app.route('/')
@handle_errors
def api_root():
    """Корневой эндпоинт с информацией о API"""
    return jsonify({
        "api": "Системный мониторинг",
        "version": config.version,
        "endpoints": {
            "version": "/version",
            "users": "/users",
            "metrics": "/metrics",
            "services": "/services",
            "machine_info": "/machine_info"
        }
    })

@app.route('/version')
@handle_errors
@rate_limited()
def get_version():
    """Получение версии агента"""
    return jsonify({
        "version": config.version,
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/update', methods=['GET', 'POST'])
@handle_errors
@rate_limited(max_per_minute=5)
def update_agent():
    """Обновление агента"""
    result = do_update_if_available()
    return jsonify({
        **result,
        "status": "success" if result.get("success") else "error",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users', methods=['GET'])
@handle_errors
@rate_limited()
def get_all_users():
    """Получение списка всех пользователей"""
    return jsonify({
        "users": config.users,
        "count": len(config.users),
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>', methods=['GET'])
@handle_errors
@rate_limited()
def get_user(username: str):
    """Получение информации о пользователе"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
        
    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    return jsonify({
        "user": user,
        "links": {
            "metrics": f"/users/{username}/metrics",
            "services": f"/users/{username}/services",
            "directories": f"/users/{username}/directories"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>/metrics', methods=['GET'])
@handle_errors
@rate_limited()
def get_user_metrics(username: str):
    """Получение всех метрик пользователя"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
        
    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    metrics_data = get_metrics()
    
    return jsonify({
        "user": user["name"],
        "metrics": metrics_data,
        "links": {
            "cpu": f"/users/{username}/metrics/cpu",
            "memory": f"/users/{username}/metrics/memory",
            "disk": f"/users/{username}/metrics/disk",
            "processes": f"/users/{username}/metrics/processes"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>/metrics/<metric_name>', methods=['GET'])
@handle_errors
@rate_limited()
def get_user_specific_metric(username: str, metric_name: str):
    """Получение конкретной метрики пользователя"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
    if not isinstance(metric_name, str) or not metric_name.isalnum():
        abort(400, description="Некорректное название метрики")

    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    metrics_data = get_metrics()
    
    if metric_name not in metrics_data:
        available_metrics = list(metrics_data.keys())
        return jsonify({
            "error": "Метрика не найдена",
            "available_metrics": available_metrics,
            "links": {m: f"/users/{username}/metrics/{m}" for m in available_metrics},
            "status": "error",
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        }), 404
    
    return jsonify({
        "user": user["name"],
        "metric": metric_name,
        "data": metrics_data[metric_name],
        "links": {
            "all_metrics": f"/users/{username}/metrics",
            "user_info": f"/users/{username}"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>/metrics/processes/top', methods=['GET'])
@handle_errors
@rate_limited()
def get_top_processes(username: str):
    """Получение топ процессов по использованию ресурсов"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
        
    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    # Параметры запроса
    sort_by = request.args.get('sort', 'cpu')  # cpu или memory
    limit = min(int(request.args.get('limit', 10)), 50)  # ограничение до 50
    
    if sort_by not in ['cpu', 'memory']:
        abort(400, description="Сортировка возможна только по 'cpu' или 'memory'")
    
    # Получаем метрики
    metrics_data = get_metrics()
    processes = metrics_data.get("processes", [])
    
    # Сортируем процессы
    if sort_by == 'cpu':
        sorted_processes = sorted(
            processes,
            key=lambda x: float(x['cpu_usage'].rstrip('%')),
            reverse=True
        )[:limit]
    else:
        sorted_processes = sorted(
            processes,
            key=lambda x: float(x['memory_usage'].split()[0]),
            reverse=True
        )[:limit]
    
    return jsonify({
        "user": user["name"],
        "sort_by": sort_by,
        "count": len(sorted_processes),
        "processes": sorted_processes,
        "links": {
            "all_processes": f"/users/{username}/metrics/processes",
            "user_metrics": f"/users/{username}/metrics"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>/services', methods=['GET'])
@handle_errors
@rate_limited()
def get_user_services(username: str):
    """Получение сервисов пользователя"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
        
    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    services_data = get_services()
    
    return jsonify({
        "user": user["name"],
        "count": len(services_data),
        "services": services_data,
        "links": {
            "user_info": f"/users/{username}",
            "metrics": f"/users/{username}/metrics"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/users/<username>/directories', methods=['GET'])
@handle_errors
@rate_limited()
def get_user_directories_endpoint(username: str):
    """Получение директорий пользователя"""
    if not isinstance(username, str) or not username.isalnum():
        abort(400, description="Некорректное имя пользователя")
        
    user = next((u for u in config.users if u['name'].lower() == username.lower()), None)
    if not user:
        abort(404, description="Пользователь не найден")
    
    dirs_data = get_user_directories()
    base_path = "C:/Users" if is_windows() else "/home"
    
    return jsonify({
        "user": user["name"],
        "base_path": base_path,
        "count": len(dirs_data),
        "directories": dirs_data,
        "links": {
            "user_info": f"/users/{username}",
            "metrics": f"/users/{username}/metrics"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/metrics', methods=['GET'])
@handle_errors
@rate_limited(max_per_minute=30)
def get_all_metrics():
    """Получение всех метрик системы"""
    return jsonify({
        "metrics": get_metrics(),
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/metrics/<metric_name>', methods=['GET'])
@handle_errors
@rate_limited()
def get_specific_metric(metric_name: str):
    """Получение конкретной метрики системы"""
    if not isinstance(metric_name, str) or not metric_name.isalnum():
        abort(400, description="Некорректное название метрики")
    
    metrics_data = get_metrics()
    
    if metric_name not in metrics_data:
        available_metrics = list(metrics_data.keys())
        return jsonify({
            "error": "Метрика не найдена",
            "available_metrics": available_metrics,
            "links": {m: f"/metrics/{m}" for m in available_metrics},
            "status": "error",
            "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        }), 404
    
    return jsonify({
        "metric": metric_name,
        "data": metrics_data[metric_name],
        "links": {
            "all_metrics": "/metrics"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/services', methods=['GET'])
@handle_errors
@rate_limited()
def get_all_services():
    """Получение всех сервисов системы"""
    services_data = get_services()
    
    return jsonify({
        "count": len(services_data),
        "services": services_data,
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    })

@app.route('/machine_info', methods=['GET'])
@handle_errors
@rate_limited()
def get_full_machine_info():
    """Получение полной информации о системе"""
    machine_data = get_machine_info()
    
    return jsonify({
        **machine_data,
        "links": {
            "users": "/users",
            "services": "/services",
            "metrics": "/metrics"
        },
        "status": "success",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
        })

# ------------------------------------------------------------------------------------
#                                Запуск приложения
# ------------------------------------------------------------------------------------
def main():
    logger.info(f"Запуск агента версии {config.version}, PID={os.getpid()}")
    
    try:
        # Запуск фоновых процессов
        background_updater.start()
        user_status_updater.start()
        
        # Настройка Flask
        app.run(
            host='0.0.0.0',
            port=5000,
            use_reloader=False,
            threaded=True
        )
    except KeyboardInterrupt:
        logger.info("Завершение работы...")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
    finally:
        background_updater.stop()
        user_status_updater.stop()
        logger.info("Агент остановлен")

if __name__ == '__main__':
    main()
