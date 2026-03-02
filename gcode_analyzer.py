import sys
import math
import os
import base64
import argparse
import pandas as pd
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QShortcut, QKeySequence
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, 
                               QHBoxLayout, QComboBox, QLineEdit, QLabel, QFrame, 
                               QCheckBox, QPushButton, QFileDialog, QTabWidget, QTextEdit)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

# =================================================================
# PART 1: ANALYZER LOGIC (Formerly gcode_analyzer.py)
# =================================================================

class GcodeAnalyzer:
    def __init__(self, filament_diameter=1.75):
        self.filament_area = math.pi * (filament_diameter / 2) ** 2
        
        # Machine State
        self.x, self.y, self.z, self.e, self.f = 0.0, 0.0, 0.0, 0.0, 0.0
        self.absolute_extrusion = True
        
        # Slicer State Trackers
        self.current_layer = 0
        self.current_type = "Custom"
        self.thumbnail_b64 = ""
        
        # Tracked Maximums
        self.max_speed_print = 0.0
        self.max_flowrate = 0.0
        
        # Data Containers
        self.data = {
            'layer': [],
            'type': [],
            'distance': [],
            'flowrate': [],
            'speed': []
        }
        
        # Log for non-movement commands
        self.command_log = []

    def parse_params(self, parts):
        params = {}
        for part in parts:
            if len(part) > 1:
                try:
                    params[part[0].upper()] = float(part[1:])
                except ValueError:
                    pass
        return params

    def analyze(self, filepath):
        in_thumbnail = False
        
        with open(filepath, 'r') as file:
            for line_num, original_line in enumerate(file, 1):
                clean_line = original_line.strip()
                
                # --- THUMBNAIL INTERCEPT ---
                if clean_line == '; THUMBNAIL_BLOCK_START':
                    in_thumbnail = True
                    continue
                elif clean_line == '; THUMBNAIL_BLOCK_END':
                    in_thumbnail = False
                    continue
                    
                if in_thumbnail:
                    if not clean_line.startswith('; thumbnail begin') and not clean_line.startswith('; thumbnail end'):
                        self.thumbnail_b64 += clean_line.lstrip('; ').strip()
                    continue

                # Intercept Slicer Comments
                if clean_line.startswith(';TYPE:'):
                    self.current_type = clean_line.split(':')[1].strip()
                    continue
                elif clean_line.startswith(';LAYER_CHANGE'):
                    self.current_layer += 1
                    continue
                
                # Strip comments for G-code parsing
                cmd_line = clean_line.split(';')[0].strip()
                if not cmd_line:
                    continue
                
                parts = cmd_line.split()
                cmd = parts[0].upper()
                params = self.parse_params(parts[1:])
                
                # --- COMMAND LOGGING (Ignore Moves) ---
                if cmd not in ('G0', 'G1', 'G2', 'G3', 'M73', 'G21', 'G90', 'M83', 'EXCLUDE_OBJECT_DEFINE'):
                    self.command_log.append(cmd_line)
                
                self.dispatch_command(cmd, params)

    def dispatch_command(self, cmd, params):
        if cmd in ('M82', 'M83'):
            self.absolute_extrusion = (cmd == 'M82')
        elif cmd in ('G0', 'G1'):
            self.handle_linear_move(cmd, params)

    def handle_linear_move(self, cmd, params):
        if 'F' in params:
            self.f = params['F'] / 60.0  # mm/s
            
        delta_e = self.get_extrusion_delta(cmd, params)
        is_print = delta_e > 0
        
        next_x = params.get('X', self.x)
        next_y = params.get('Y', self.y)
        distance = math.sqrt((next_x - self.x)**2 + (next_y - self.y)**2)
        
        # Track valid extrusion moves
        if is_print and distance > 0 and self.f > 0:
            time_seconds = distance / self.f
            volume_mm3 = delta_e * self.filament_area
            flowrate = volume_mm3 / time_seconds
            
            if flowrate > self.max_flowrate:
                self.max_flowrate = flowrate
            if self.f > self.max_speed_print:
                self.max_speed_print = self.f
            
            self.data['layer'].append(self.current_layer)
            self.data['type'].append(self.current_type)
            self.data['distance'].append(distance)
            self.data['flowrate'].append(flowrate)
            self.data['speed'].append(self.f)
        
        if 'X' in params: self.x = params['X']
        if 'Y' in params: self.y = params['Y']
        if 'Z' in params: self.z = params['Z']

    def get_extrusion_delta(self, cmd, params):
        if cmd == 'G0' or 'E' not in params:
            return 0.0
        if self.absolute_extrusion:
            delta_e = params['E'] - self.e
            self.e = params['E']
        else:
            delta_e = params['E']
        return delta_e

    def get_summary(self):
        return {
            'total_layers': self.current_layer,
            'total_moves': len(self.data['distance']),
            'max_flow': self.max_flowrate,
            'max_speed': self.max_speed_print,
            'thumbnail_b64': self.thumbnail_b64,
            'command_log': self.command_log
        }

# =================================================================
# PART 2: GUI INTERFACE (Formerly gcode_analyzer_gui.py)
# =================================================================

class MacroViewer(QWidget):
    def __init__(self, command_log=[], parent=None):
        super().__init__(parent)
        self.command_log = command_log
        layout = QVBoxLayout(self)
        
        btn_layout = QHBoxLayout()
        self.btn_view1 = QPushButton("Unique Commands (No Vars)")
        self.btn_view2 = QPushButton("Unique Full Lines")
        self.btn_view3 = QPushButton("Chronological")
        self.btn_copy = QPushButton("Copy to Clipboard")
        self.btn_copy.setStyleSheet("background-color: #0078d7; color: white; font-weight: bold;")
        
        btn_layout.addWidget(self.btn_view1)
        btn_layout.addWidget(self.btn_view2)
        btn_layout.addWidget(self.btn_view3)
        btn_layout.addStretch()
        btn_layout.addWidget(self.btn_copy)
        
        self.text_area = QTextEdit()
        self.text_area.setReadOnly(True)
        self.text_area.setStyleSheet("font-family: Monospace; font-size: 11px;")
        
        layout.addLayout(btn_layout)
        layout.addWidget(self.text_area)
        
        self.btn_view1.clicked.connect(self.show_view_1)
        self.btn_view2.clicked.connect(self.show_view_2)
        self.btn_view3.clicked.connect(self.show_view_3)
        self.btn_copy.clicked.connect(self.copy_to_clipboard)
        self.show_view_3()

    def update_log(self, new_log):
        self.command_log = new_log
        self.show_view_3()

    def show_view_1(self):
        if not self.command_log: return
        cmds = set(line.split()[0] for line in self.command_log if line)
        self.text_area.setText("\n".join(sorted(list(cmds))))
        
    def show_view_2(self):
        if not self.command_log: return
        self.text_area.setText("\n".join(sorted(list(set(self.command_log)))))

    def show_view_3(self):
        if not self.command_log:
            self.text_area.setText("No macros or non-movement commands found.")
            return
        self.text_area.setText("\n".join(self.command_log))

    def copy_to_clipboard(self):
        QApplication.clipboard().setText(self.text_area.toPlainText())

class InfoBlock(QFrame):
    def __init__(self, summary_data=None, parent=None):
        super().__init__(parent)
        self.setFrameShape(QFrame.StyledPanel)
        layout = QHBoxLayout(self)
        
        self.lbl_thumbnail = QLabel("No Thumbnail")
        self.lbl_thumbnail.setAlignment(Qt.AlignCenter)
        self.lbl_thumbnail.setFixedSize(200, 200)
        self.lbl_thumbnail.setStyleSheet("background-color: #2b2b2b; color: #888;")
        
        stats_layout = QVBoxLayout()
        stats_layout.setAlignment(Qt.AlignTop)
        
        title = QLabel("<b>G-Code Print Summary</b>")
        title.setStyleSheet("font-size: 16px; margin-bottom: 10px;")
        
        self.lbl_layers = QLabel()
        self.lbl_moves = QLabel()
        self.lbl_max_flow = QLabel()
        self.lbl_max_speed = QLabel()
        
        stats_layout.addWidget(title)
        stats_layout.addWidget(self.lbl_layers)
        stats_layout.addWidget(self.lbl_moves)
        stats_layout.addWidget(self.lbl_max_flow)
        stats_layout.addWidget(self.lbl_max_speed)
        
        layout.addWidget(self.lbl_thumbnail)
        layout.addLayout(stats_layout)
        layout.addStretch()

        if summary_data:
            self.update_info(summary_data)

    def update_info(self, summary_data):
        self.lbl_layers.setText(f"<b>Total Layers:</b> {summary_data.get('total_layers', 0)}")
        self.lbl_moves.setText(f"<b>Extrusion Moves:</b> {summary_data.get('total_moves', 0)}")
        self.lbl_max_flow.setText(f"<b>Global Max Flow:</b> {summary_data.get('max_flow', 0):.2f} mm³/s")
        self.lbl_max_speed.setText(f"<b>Global Max Speed:</b> {summary_data.get('max_speed', 0):.2f} mm/s")
        
        b64_string = summary_data.get('thumbnail_b64', '')
        if b64_string:
            try:
                img_bytes = base64.b64decode(b64_string)
                pixmap = QPixmap()
                pixmap.loadFromData(img_bytes) 
                self.lbl_thumbnail.setPixmap(pixmap.scaled(200, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation))
            except:
                self.lbl_thumbnail.setText("Thumbnail Error")
        else:
            self.lbl_thumbnail.clear()
            self.lbl_thumbnail.setText("No Thumbnail")

class PlotBlock(QWidget):
    control_updated = Signal(str, object) 

    def __init__(self, data_frame, parent=None):
        super().__init__(parent)
        self.df = data_frame
        self._is_updating = True 
        self.cursor_toggle = True
        self.hp_threshold = 0.0
        
        layout = QVBoxLayout(self)
        control_layout = QHBoxLayout()
        
        self.chk_sync_type = QCheckBox("Sync")
        self.combo_type = QComboBox()
        self.combo_type.addItems(["flowrate", "speed"])
        
        self.chk_sync_sort = QCheckBox("Sync")
        self.combo_sort = QComboBox()
        self.combo_sort.addItems(["Chronological", "Sorted Ascending"])
        
        self.chk_sync_process = QCheckBox("Sync")
        self.combo_process = QComboBox()
        
        self.chk_sync_layer = QCheckBox("Sync")
        self.input_layers = QLineEdit("all")
        self.input_layers.setFixedWidth(120)

        self.chk_normalize = QCheckBox("Normalize (Include 0)")
        
        def add_control(label_text, widget, sync_cb):
            control_layout.addWidget(sync_cb)
            control_layout.addWidget(QLabel(label_text))
            control_layout.addWidget(widget)
            control_layout.addSpacing(15)
            
        add_control("Y-Axis:", self.combo_type, self.chk_sync_type)
        add_control("X-Axis:", self.combo_sort, self.chk_sync_sort)
        add_control("Feature:", self.combo_process, self.chk_sync_process)
        add_control("Layers:", self.input_layers, self.chk_sync_layer)
        control_layout.addWidget(self.chk_normalize)
        control_layout.addStretch()
        
        self.combo_type.currentTextChanged.connect(lambda v: self.on_control_change('type', v))
        self.combo_sort.currentTextChanged.connect(lambda v: self.on_control_change('sort', v))
        self.combo_process.currentTextChanged.connect(lambda v: self.on_control_change('process', v))
        self.input_layers.editingFinished.connect(self.on_layer_change)
        self.chk_normalize.toggled.connect(lambda: self.update_plot())
        
        self.lbl_cursors = QLabel("<b>Left-Click:</b> Cursors | <b>Right-Click:</b> Filter")
        self.lbl_cursors.setStyleSheet("color: #0078d7;")
        self.cursor_a_pos, self.cursor_b_pos = None, None
        self.cursor_a_lines, self.cursor_b_lines = None, None
        
        self.fig = Figure(figsize=(5, 3), dpi=100)
        self.ax = self.fig.add_subplot(111)
        self.canvas = FigureCanvasQTAgg(self.fig)
        self.canvas.mpl_connect('button_press_event', self.on_canvas_click)
        
        layout.addLayout(control_layout)
        layout.addWidget(self.lbl_cursors)
        layout.addWidget(self.canvas)
        
        self.refresh_comboboxes()
        self._is_updating = False
        self.update_plot()

    def update_data(self, new_df):
        self._is_updating = True
        self.df = new_df
        self.hp_threshold = 0.0
        self.refresh_comboboxes()
        self._is_updating = False
        self.update_plot()

    def refresh_comboboxes(self):
        curr = self.combo_process.currentText()
        self.combo_process.clear()
        types = ["All"] + sorted(self.df['type'].unique().tolist()) if not self.df.empty else ["All"]
        self.combo_process.addItems(types)
        if curr in types: self.combo_process.setCurrentText(curr)

    def on_control_change(self, field, value):
        if self._is_updating: return
        if field == 'type': self.hp_threshold = 0.0
        self.update_plot()
        if getattr(self, f"chk_sync_{field}").isChecked():
            self.control_updated.emit(field, value)
            
    def on_layer_change(self):
        if self._is_updating: return
        self.update_plot()
        if self.chk_sync_layer.isChecked():
            self.control_updated.emit('layer', self.input_layers.text())

    def external_sync(self, field, value):
        if not getattr(self, f"chk_sync_{field}").isChecked(): return
        self._is_updating = True
        if field == 'type': self.combo_type.setCurrentText(value)
        elif field == 'sort': self.combo_sort.setCurrentText(value)
        elif field == 'process': self.combo_process.setCurrentText(value)
        elif field == 'layer': self.input_layers.setText(value)
        self._is_updating = False
        self.update_plot()

    def on_canvas_click(self, event):
        if event.inaxes != self.ax: return 
        if event.button == 1:
            if self.cursor_toggle: self.cursor_a_pos = (event.xdata, event.ydata)
            else: self.cursor_b_pos = (event.xdata, event.ydata)
            self.cursor_toggle = not self.cursor_toggle
            self.draw_cursors()
        elif event.button == 3:
            self.hp_threshold = max(0, event.ydata)
            self.update_plot()

    def draw_cursors(self):
        for lines in [self.cursor_a_lines, self.cursor_b_lines]:
            if lines: 
                lines[0].remove(); lines[1].remove()
        self.cursor_a_lines = self.cursor_b_lines = None
        
        txt = [f"<b>Filter:</b> > {self.hp_threshold:.2f}" if self.hp_threshold > 0 else "<b>Filter:</b> OFF"]
        if self.cursor_a_pos:
            self.cursor_a_lines = (self.ax.axvline(self.cursor_a_pos[0], color='red', linestyle='--'),
                                   self.ax.axhline(self.cursor_a_pos[1], color='red', linestyle='--'))
            txt.append(f"<span style='color:red;'>A: ({self.cursor_a_pos[0]:.1f}, {self.cursor_a_pos[1]:.2f})</span>")
        if self.cursor_b_pos:
            self.cursor_b_lines = (self.ax.axvline(self.cursor_b_pos[0], color='blue', linestyle='--'),
                                   self.ax.axhline(self.cursor_b_pos[1], color='blue', linestyle='--'))
            txt.append(f"<span style='color:blue;'>B: ({self.cursor_b_pos[0]:.1f}, {self.cursor_b_pos[1]:.2f})</span>")
        self.lbl_cursors.setText("   |   ".join(txt))
        self.canvas.draw()

    def update_plot(self):
        y_col, sort_mode, process_type = self.combo_type.currentText(), self.combo_sort.currentText(), self.combo_process.currentText()
        if self.df.empty:
            self.ax.clear(); self.ax.text(0.5, 0.5, "No Data", ha='center'); self.canvas.draw(); return

        mask = pd.Series(True, index=self.df.index)
        layer_str = self.input_layers.text().strip().lower()
        if layer_str != 'all' and layer_str:
            try:
                if '-' in layer_str:
                    s, e = layer_str.split('-')
                    mask = (self.df['layer'] >= int(s)) & (self.df['layer'] <= (int(e) if e else self.df['layer'].max()))
                else: mask = self.df['layer'] == int(layer_str)
            except: pass
            
        if process_type != "All": mask &= (self.df['type'] == process_type)
        fdf = self.df[mask].copy()
        if self.hp_threshold > 0: fdf = fdf[fdf[y_col] >= self.hp_threshold]
        
        self.ax.clear()
        if fdf.empty: self.canvas.draw(); return
            
        if sort_mode == "Sorted Ascending": fdf = fdf.sort_values(by=y_col)
        x, y = fdf['distance'].cumsum(), fdf[y_col]
        color = 'tab:blue' if y_col == 'flowrate' else 'tab:red'
        self.ax.plot(x, y, color=color, linewidth=1)
        if sort_mode == "Sorted Ascending": self.ax.fill_between(x, y, alpha=0.3, color=color)
        if self.chk_normalize.isChecked():
            self.ax.set_ylim(bottom=0, top=self.df[y_col].max() * 1.05)
        self.ax.grid(True, linestyle='--', alpha=0.6)
        self.fig.tight_layout(); self.canvas.draw()

class MainWindow(QMainWindow):
    def __init__(self, df=pd.DataFrame(), summary_data={}, command_log=[]):
        super().__init__()
        self.setWindowTitle("G-Code Kinematics Analyzer - (Ctrl+I to Import)")
        self.resize(1100, 950)
        
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)
        
        self.tab_kinematics = QWidget()
        kin_layout = QVBoxLayout(self.tab_kinematics)
        self.info_block = InfoBlock(summary_data)
        self.info_block.setFixedHeight(220)
        kin_layout.addWidget(self.info_block)
        
        self.plot_blocks = []
        for _ in range(2):
            block = PlotBlock(df)
            block.control_updated.connect(self.route_sync_signal)
            self.plot_blocks.append(block); kin_layout.addWidget(block)
            
        self.tabs.addTab(self.tab_kinematics, "Kinematics")
        self.tab_macros = MacroViewer(command_log)
        self.tabs.addTab(self.tab_macros, "Macros & Commands")
        
        QShortcut(QKeySequence("Ctrl+I"), self).activated.connect(self.open_file_dialog)

    def route_sync_signal(self, field, value):
        for block in self.plot_blocks:
            if block != self.sender(): block.external_sync(field, value)

    def open_file_dialog(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select G-Code", os.path.expanduser("~/Downloads"), "G-Code (*.gcode *.nc)")
        if path: self.load_gcode(path)

    def load_gcode(self, filepath):
        analyzer = GcodeAnalyzer()
        analyzer.analyze(filepath)
        summary = analyzer.get_summary()
        df = pd.DataFrame(analyzer.data)
        self.setWindowTitle(f"G-Code Analyzer - {os.path.basename(filepath)}")
        self.info_block.update_info(summary)
        for block in self.plot_blocks: block.update_data(df)
        self.tab_macros.update_log(summary.get('command_log', []))

if __name__ == "__main__":
    app = QApplication(sys.argv)
    parser = argparse.ArgumentParser()
    parser.add_argument("file", nargs="?")
    args = parser.parse_args()

    if args.file:
        analyzer = GcodeAnalyzer()
        analyzer.analyze(args.file)
        summary = analyzer.get_summary()
        window = MainWindow(pd.DataFrame(analyzer.data), summary, summary.get('command_log', []))
    else:
        window = MainWindow()
    window.show()
    sys.exit(app.exec())
