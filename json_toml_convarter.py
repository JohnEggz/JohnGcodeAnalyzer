import sys
import os
import json
import toml
from collections import OrderedDict
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                             QHBoxLayout, QListWidget, QPushButton, QLabel, 
                             QFileDialog, QSplitter, QMessageBox, QFrame)
from PySide6.QtCore import Qt

# --- CONFIGURATION PATHS ---
CONFIG_DIR = "config_json_toml"
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR)

SETTINGS_FILE = os.path.join(CONFIG_DIR, "gui_settings.json")
CONFIG_FILE = os.path.join(CONFIG_DIR, "mapping.json")
TYPE_MAP_FILE = os.path.join(CONFIG_DIR, "type_map.json")

# --- CORE CONVERSION LOGIC ---

def try_numeric(val):
    if not isinstance(val, str): return val
    try:
        if "." in val: return float(val)
        return int(val)
    except (ValueError, TypeError):
        return val

def clean_value(v):
    if isinstance(v, list):
        if len(v) == 1: return try_numeric(v[0])
        return [try_numeric(i) for i in v]
    return try_numeric(v)

def load_json_ordered(path):
    if not os.path.exists(path): return OrderedDict()
    with open(path, 'r') as f:
        return json.load(f, object_pairs_hook=OrderedDict)

def save_type_info(data):
    type_info = {}
    if os.path.exists(TYPE_MAP_FILE):
        with open(TYPE_MAP_FILE, 'r') as f:
            type_info = json.load(f)
    for k, v in data.items():
        type_info[k] = "list" if isinstance(v, list) else "string"
    with open(TYPE_MAP_FILE, 'w') as f:
        json.dump(type_info, f, indent=4)

def orca_ify(key, value):
    if not os.path.exists(TYPE_MAP_FILE):
        return [str(value)] if isinstance(value, list) else str(value)
    with open(TYPE_MAP_FILE, 'r') as f:
        type_info = json.load(f)
    expected_type = type_info.get(key, "string")
    if expected_type == "list":
        if isinstance(value, list): return [str(i) for i in value]
        return [str(value)]
    else:
        if isinstance(value, list): return str(value[0]) if value else ""
        return str(value)

def load_mapping():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, 'r') as f:
            return json.load(f, object_pairs_hook=OrderedDict)
    return OrderedDict()

# --- REFACTORED CONVERSION LOGIC WITH PATH REPLICATION ---

def run_json_to_toml(input_path, source_root, target_root):
    """Converts JSON to TOML preserving the relative directory structure."""
    data = load_json_ordered(input_path)
    save_type_info(data)
    mapping = load_mapping()
    output_data = OrderedDict()
    assigned_keys = set()

    for group, keys in mapping.items():
        group_dict = OrderedDict()
        for k in keys:
            if k in data:
                group_dict[k] = clean_value(data[k])
                assigned_keys.add(k)
        if group_dict: output_data[group] = group_dict

    undefined = OrderedDict({k: clean_value(v) for k, v in data.items() if k not in assigned_keys})
    if undefined: output_data["undefined"] = undefined

    # Calculate relative path to replicate structure
    rel_path = os.path.relpath(input_path, source_root)
    file_name = rel_path.replace(".json", ".toml")
    target_path = os.path.join(target_root, file_name)
    
    # Ensure the subdirectory exists in the target
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    
    with open(target_path, 'w') as f:
        toml.dump(output_data, f)

def run_toml_to_json(input_path, source_root, target_root):
    """Converts TOML to JSON preserving the relative directory structure."""
    with open(input_path, 'r') as f:
        toml_data = toml.load(f, _dict=OrderedDict)
    
    flat_data = OrderedDict()
    for group in toml_data.values():
        if isinstance(group, dict):
            for k, v in group.items():
                flat_data[k] = orca_ify(k, v)
                
    # Calculate relative path to replicate structure
    rel_path = os.path.relpath(input_path, source_root)
    file_name = rel_path.replace(".toml", ".json")
    target_path = os.path.join(target_root, file_name)
    
    # Ensure the subdirectory exists in the target
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    
    with open(target_path, 'w') as f:
        json.dump(flat_data, f, indent=4)

def update_mapping_append(toml_path):
    existing_mapping = load_mapping()
    with open(toml_path, 'r') as f:
        current_toml = toml.load(f, _dict=OrderedDict)
    updated_mapping = OrderedDict()
    keys_in_current_toml = set()
    for group, content in current_toml.items():
        if group.lower() == "undefined" or not isinstance(content, dict): continue
        updated_mapping[group] = list(content.keys())
        keys_in_current_toml.update(content.keys())
    for old_group, old_keys in existing_mapping.items():
        if old_group not in updated_mapping:
            filtered = [k for k in old_keys if k not in keys_in_current_toml]
            if filtered: updated_mapping[old_group] = filtered
        else:
            for ok in old_keys:
                if ok not in keys_in_current_toml and ok not in updated_mapping[old_group]:
                    updated_mapping[old_group].append(ok)
    with open(CONFIG_FILE, 'w') as f:
        json.dump(updated_mapping, f, indent=4)

# --- GUI CLASS ---

class ConverterGUI(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TOML ⇋ JSON Structure Sync")
        self.resize(1100, 800)
        
        self.settings = self.load_settings()
        self.current_toml_dir = self.settings.get("toml_path", os.getcwd())
        self.current_json_dir = self.settings.get("json_path", os.getcwd())
        
        self.init_ui()
        self.refresh_lists()

    def load_settings(self):
        if os.path.exists(SETTINGS_FILE):
            try:
                with open(SETTINGS_FILE, 'r') as f:
                    return json.load(f)
            except: return {}
        return {}

    def save_settings(self):
        with open(SETTINGS_FILE, 'w') as f:
            json.dump({"toml_path": self.current_toml_dir, "json_path": self.current_json_dir}, f)

    def init_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        self.btn_sync_cfg = QPushButton("Sync Mapping (Build from all TOMLs in Left Panel)")
        self.btn_sync_cfg.setStyleSheet("font-weight: bold; padding: 8px; background-color: #34495e; color: white;")
        self.btn_sync_cfg.clicked.connect(self.sync_config)
        main_layout.addWidget(self.btn_sync_cfg)

        splitter = QSplitter(Qt.Horizontal)

        # LEFT: TOML Panel
        toml_container = QFrame()
        toml_vbox = QVBoxLayout(toml_container)
        btn_browse_toml = QPushButton("📂 Select TOML Folder/Files")
        self.lbl_toml_path = QLabel(f"<b>Root Path:</b> {self.current_toml_dir}")
        self.lbl_toml_path.setWordWrap(True)
        self.toml_list = QListWidget()
        self.toml_list.setSelectionMode(QListWidget.MultiSelection)
        self.btn_t2j_sel = QPushButton("Convert Selected TOML ➔")
        self.btn_t2j_all = QPushButton("Convert All TOML ➔")
        
        toml_vbox.addWidget(QLabel("<h3>TOML Source</h3>"))
        toml_vbox.addWidget(btn_browse_toml)
        toml_vbox.addWidget(self.lbl_toml_path)
        toml_vbox.addWidget(self.toml_list)
        toml_vbox.addWidget(self.btn_t2j_sel)
        toml_vbox.addWidget(self.btn_t2j_all)

        # RIGHT: JSON Panel
        json_container = QFrame()
        json_vbox = QVBoxLayout(json_container)
        btn_browse_json = QPushButton("📂 Select JSON Folder/Files")
        self.lbl_json_path = QLabel(f"<b>Root Path:</b> {self.current_json_dir}")
        self.lbl_json_path.setWordWrap(True)
        self.json_list = QListWidget()
        self.json_list.setSelectionMode(QListWidget.MultiSelection)
        self.btn_j2t_sel = QPushButton("⬅ Convert Selected JSON")
        self.btn_j2t_all = QPushButton("⬅ Convert All JSON")
        
        json_vbox.addWidget(QLabel("<h3>JSON Source</h3>"))
        json_vbox.addWidget(btn_browse_json)
        json_vbox.addWidget(self.lbl_json_path)
        json_vbox.addWidget(self.json_list)
        json_vbox.addWidget(self.btn_j2t_sel)
        json_vbox.addWidget(self.btn_j2t_all)

        splitter.addWidget(toml_container)
        splitter.addWidget(json_container)
        main_layout.addWidget(splitter)

        # --- CONNECTIONS ---
        btn_browse_toml.clicked.connect(lambda: self.pick_target("toml"))
        btn_browse_json.clicked.connect(lambda: self.pick_target("json"))
        self.btn_t2j_sel.clicked.connect(lambda: self.process("t2j", False))
        self.btn_t2j_all.clicked.connect(lambda: self.process("t2j", True))
        self.btn_j2t_sel.clicked.connect(lambda: self.process("j2t", False))
        self.btn_j2t_all.clicked.connect(lambda: self.process("j2t", True))

    def pick_target(self, side):
        dialog = QFileDialog(self)
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, False)
        if dialog.exec():
            selected = dialog.selectedFiles()
            if selected:
                path = selected[0] if os.path.isdir(selected[0]) else os.path.dirname(selected[0])
                if side == "toml":
                    self.current_toml_dir = path
                    self.lbl_toml_path.setText(f"<b>Root Path:</b> {path}")
                else:
                    self.current_json_dir = path
                    self.lbl_json_path.setText(f"<b>Root Path:</b> {path}")
                self.save_settings()
                self.refresh_lists()

    def refresh_lists(self):
        self.toml_list.clear()
        if os.path.exists(self.current_toml_dir):
            for root, _, files in os.walk(self.current_toml_dir):
                for f in sorted(files):
                    if f.endswith(".toml"):
                        # Show relative paths to visual structure
                        self.toml_list.addItem(os.path.relpath(os.path.join(root, f), self.current_toml_dir))
        
        self.json_list.clear()
        if os.path.exists(self.current_json_dir):
            for root, _, files in os.walk(self.current_json_dir):
                for f in sorted(files):
                    if f.endswith(".json"):
                        self.json_list.addItem(os.path.relpath(os.path.join(root, f), self.current_json_dir))

    def process(self, mode, all_files):
        try:
            if mode == "t2j":
                items = [self.toml_list.item(i).text() for i in range(self.toml_list.count())] if all_files \
                        else [i.text() for i in self.toml_list.selectedItems()]
                for rel in items:
                    source_full = os.path.join(self.current_toml_dir, rel)
                    run_toml_to_json(source_full, self.current_toml_dir, self.current_json_dir)
            else:
                items = [self.json_list.item(i).text() for i in range(self.json_list.count())] if all_files \
                        else [i.text() for i in self.json_list.selectedItems()]
                for rel in items:
                    source_full = os.path.join(self.current_json_dir, rel)
                    run_json_to_toml(source_full, self.current_json_dir, self.current_toml_dir)
            self.refresh_lists()
            QMessageBox.information(self, "Success", f"Processed {len(items)} files with structure preserved.")
        except Exception as e:
            QMessageBox.critical(self, "Error", str(e))

    def sync_config(self):
        items = [self.toml_list.item(i).text() for i in range(self.toml_list.count())]
        for rel in items:
            update_mapping_append(os.path.join(self.current_toml_dir, rel))
        QMessageBox.information(self, "Success", f"Mapping configuration updated in {CONFIG_DIR}/")

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ConverterGUI()
    window.show()
    sys.exit(app.exec())
