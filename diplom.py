# -*- coding: utf-8 -*-

import os
import subprocess
import psutil
import platform
import time
from flask import Flask, jsonify, abort
import requests

app = Flask(__name__)

users = [
    {"name": "Alice", "ip": "172.19.0.1"},
    {"name": "Bob", "ip": "192.168.1.11"},
    {"name": "Charlie", "ip": "192.168.1.12"}
]

REPO_URL = "https://raw.githubusercontent.com/username/repository/main/agent.py" # URL репозитория с кодом агента

def convert_bytes(bytes_value):
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if bytes_value < 1024.0:
            return f"{bytes_value:.2f} {unit}"
        bytes_value /= 1024.0

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
            "Description": "Использование CPU и памяти для процесса"
        })

    last_update = time.strftime('%Y-%m-%d %H:%M:%S')

    metrics = {
        "last_update": last_update,
        "system_info": {
            "Operating System": f"{platform.system()} {platform.version()}",
            "Architecture": platform.architecture()[0],
            "Hostname": platform.node()
        },
        "cpu": {
            "Usage": f"{cpu_usage}%",
            "Description": "Текущая загрузка процессора в процентах."
        },
        "memory": {
            "Total": convert_bytes(memory_info.total),
            "Used": convert_bytes(memory_info.used),
            "Free": convert_bytes(memory_info.free),
            "Usage": f"{memory_info.percent}%",
            "Description": "Общая память, использованная и свободная память, а также процент использования."
        },
        "disk": {
            "Total": convert_bytes(disk_info.total),
            "Used": convert_bytes(disk_info.used),
            "Free": convert_bytes(disk_info.free),
            "Usage": f"{disk_info.percent}%",
            "Description": "Информация о дисковом пространстве."
        },
        "processes": processes
    }

    return metrics

def get_users():
    return users

def get_user_directories():
    system = platform.system().lower()
    
    if system == 'windows':
        user_dirs = [d for d in os.listdir("C:/Users") if os.path.isdir(os.path.join("C:/Users", d))]
        return user_dirs

    elif system == 'linux':
        user_dirs = [d for d in os.listdir("/home") if os.path.isdir(os.path.join("/home", d))]
        return user_dirs

    return []

@app.route('/users', methods=['GET'])
def list_users():
    users_list = get_users()
    return jsonify({"users": users_list})

@app.route('/connect/<username>', methods=['GET'])
def connect_to_user(username):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")

    user_metrics = get_metrics()
    user_dirs = get_user_directories()
    
    return jsonify({
        "user": user,
        "metrics": user_metrics,
        "directories": user_dirs
    })

@app.route('/connect/<username>/<metric_name>', methods=['GET'])
def connect_to_user_metric(username, metric_name):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")

    metrics_data = get_metrics()

    if metric_name in metrics_data:
        return jsonify({metric_name: metrics_data[metric_name]})
    else:
        abort(404, description="Metric not found")

@app.route('/connect/<username>/directories', methods=['GET'])
def connect_to_user_directories(username):
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    user_dirs = get_user_directories()

    return jsonify({"directories": user_dirs})

@app.route('/metrics/cpu', methods=['GET'])
def get_cpu_metrics():
    metrics_data = get_metrics()
    return jsonify({"cpu": metrics_data["cpu"]})

@app.route('/metrics/memory', methods=['GET'])
def get_memory_metrics():
    metrics_data = get_metrics()
    return jsonify({"memory": metrics_data["memory"]})

@app.route('/metrics/disk', methods=['GET'])
def get_disk_metrics():
    metrics_data = get_metrics()
    return jsonify({"disk": metrics_data["disk"]})

@app.route('/metrics/processes', methods=['GET'])
def get_processes_metrics():
    metrics_data = get_metrics()
    return jsonify({"processes": metrics_data["processes"]})

@app.route('/metrics/system_info', methods=['GET'])
def get_system_info_metrics():
    metrics_data = get_metrics()
    return jsonify({"system_info": metrics_data["system_info"]})

@app.route('/metrics/list', methods=['GET'])
def list_metrics():
    available_metrics = ["cpu", "memory", "disk", "processes", "system_info"]
    return jsonify({"available_metrics": available_metrics})

@app.route('/metrics', methods=['GET'])
def metrics():
    metrics_data = get_metrics()
    return jsonify(metrics_data)

def get_services():
    services = []
    if platform.system().lower() == "windows":
        for service in psutil.win_service_iter():
            services.append({
                "name": service.name(),
                "status": service.status(),
                "display_name": service.display_name()
            })
    elif platform.system().lower() == "linux":
        result = subprocess.run(["systemctl", "list-units", "--type=service", "--no-pager", "--no-legend"], 
                                stdout=subprocess.PIPE, text=True)
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
    user = next((u for u in get_users() if u['name'] == username), None)
    if not user:
        abort(404, description="User not found")
    return jsonify({"user": user, "services": get_services()})

# Функция для обновления агента из репозитория
def self_update():
    current_file = __file__
    new_file = "agent.py"

    try:
        response = requests.get(REPO_URL)
        if response.status_code == 200:
            with open(new_file, "w") as f:
                f.write(response.text)

            # Запускаем новый процесс
            subprocess.Popen(["python3", new_file])

            # Завершаем старый процесс
            os._exit(0)

        else:
            return f"Failed to download update. Status code: {response.status_code}"
    except Exception as e:
        return str(e)

def update_all_agents():
    update_results = {}
    for user in users:
        try:
            response = requests.post(f"http://{user['ip']}:5000/self-update", timeout=5)
            update_results[user['name']] = response.json()
        except requests.exceptions.RequestException as e:
            update_results[user['name']] = f"Failed to update: {str(e)}"
    return update_results

@app.route('/update', methods=['POST'])
def update_agents():
    result = update_all_agents()
    return jsonify({"status": "updating all agents", "results": result})

@app.route('/self-update', methods=['POST'])
def update_self():
    result = self_update()
    return jsonify({"status": "self-updating", "message": result})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
