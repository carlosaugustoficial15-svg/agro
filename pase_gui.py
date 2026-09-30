"""Graphical configuration and execution interface for PASE on Windows."""

from __future__ import annotations

import os
import queue
import json
import math
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, simpledialog, ttk

import yaml


ROOT = Path(__file__).resolve().parent
CONFIGS = {
    "Local e clima": ROOT / "INPUTS" / "SCENARIOS" / "Example1_loc.yaml",
    "Sistema agrivoltaico": ROOT / "INPUTS" / "AV_CENTRAL" / "Example1_AV.yaml",
    "Módulo fotovoltaico": ROOT / "INPUTS" / "HARDWARE" / "PV_MODULES" / "Example1_PV_Module.yaml",
    "Estrutura de suporte": ROOT / "INPUTS" / "HARDWARE" / "STRUCTURES" / "agrivoltaic_fence.yaml",
    "Cultura e solo": ROOT / "INPUTS" / "CROPS" / "config" / "simple_example.yml",
    "Parâmetros da cultura": ROOT / "INPUTS" / "CROPS" / "SIMPLE" / "crop_init.yaml",
    "Parâmetros do solo": ROOT / "INPUTS" / "CROPS" / "SIMPLE" / "soil_init.yaml",
}
PRESETS = ROOT / "SIMULACOES"


class ConfigTab(ttk.Frame):
    def __init__(self, parent: ttk.Notebook, path: Path, on_change=None):
        super().__init__(parent)
        self.path = path
        self.data: dict = {}
        self.variables: dict[str, tk.Variable] = {}
        self.on_change = on_change
        self.canvas = tk.Canvas(self, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=12)
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self.body.bind("<Configure>", lambda _e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.load()

    def load(self) -> None:
        with self.path.open("r", encoding="utf-8") as stream:
            self.data = yaml.safe_load(stream)
        for child in self.body.winfo_children():
            child.destroy()
        self.variables.clear()
        for row, (name, meta) in enumerate(self.data.items()):
            ttk.Label(self.body, text=name, font=("Segoe UI", 10, "bold")).grid(
                row=row * 2, column=0, sticky="nw", padx=(0, 15), pady=(8, 0)
            )
            value, value_type = meta.get("Value"), meta.get("Type", "string")
            variable: tk.Variable
            if value_type == "boolean":
                variable = tk.BooleanVar(value=value)
                widget = ttk.Checkbutton(self.body, variable=variable)
            elif meta.get("Possibilities") or meta.get("Choices"):
                variable = tk.StringVar(value=str(value))
                widget = ttk.Combobox(
                    self.body, textvariable=variable,
                    values=meta.get("Possibilities", meta.get("Choices")), state="readonly"
                )
            else:
                variable = tk.StringVar(value=str(value))
                widget = ttk.Entry(self.body, textvariable=variable)
            widget.grid(row=row * 2, column=1, sticky="ew", pady=(8, 0))
            unit = meta.get("Unit", meta.get("Units", ""))
            description = meta.get("Definition", "")
            limits = meta.get("Limit")
            hint = f"{description}"
            if unit:
                hint += f"  Unidade: {unit}."
            if limits:
                hint += f"  Limites: {limits}."
            ttk.Label(self.body, text=hint, foreground="#555", wraplength=760).grid(
                row=row * 2 + 1, column=0, columnspan=2, sticky="w", pady=(1, 5)
            )
            self.variables[name] = variable
            if self.on_change:
                variable.trace_add("write", lambda *_args: self.on_change())
        self.body.columnconfigure(1, weight=1)

    def values(self) -> dict:
        result = {}
        for name, variable in self.variables.items():
            raw = variable.get()
            value_type = self.data[name].get("Type")
            try:
                result[name] = int(raw) if value_type == "integer" else float(raw) if value_type == "float" else raw
            except (TypeError, ValueError):
                result[name] = raw
        return result

    def save(self) -> None:
        for name, variable in self.variables.items():
            meta = self.data[name]
            raw = variable.get()
            try:
                if meta["Type"] == "integer":
                    value = int(raw)
                elif meta["Type"] == "float":
                    value = float(raw)
                elif meta["Type"] == "boolean":
                    value = bool(raw)
                elif meta["Type"] == "list":
                    value = yaml.safe_load(raw)
                    if not isinstance(value, list):
                        raise ValueError("é necessário informar uma lista")
                else:
                    value = str(raw)
            except (TypeError, ValueError) as exc:
                raise ValueError(f'Valor inválido em "{name}": {exc}') from exc
            limits = meta.get("Limit")
            if limits and all(isinstance(item, (int, float)) for item in limits):
                if not limits[0] <= value <= limits[1]:
                    raise ValueError(f'"{name}" deve estar entre {limits[0]} e {limits[1]}.')
            meta["Value"] = value
        with self.path.open("w", encoding="utf-8") as stream:
            yaml.safe_dump(self.data, stream, sort_keys=False, allow_unicode=True, width=100)


class ScenePreview(ttk.Frame):
    """Interactive VTK viewport that mirrors the main agrivoltaic geometry."""

    def __init__(self, parent):
        super().__init__(parent)
        self.available = False
        self.initial_camera = True
        self.last_values = {}
        try:
            import vtkmodules.vtkRenderingOpenGL2  # noqa: F401
            from vtkmodules.tk.vtkTkRenderWindowInteractor import vtkTkRenderWindowInteractor
            from vtkmodules.vtkRenderingCore import vtkRenderer

            self.widget = vtkTkRenderWindowInteractor(self, width=700, height=600)
            self.widget.pack(fill="both", expand=True)
            self.renderer = vtkRenderer()
            self.renderer.SetBackground(0.055, 0.075, 0.11)
            self.widget.GetRenderWindow().AddRenderer(self.renderer)
            self.widget.Initialize()
            self.available = True
        except Exception:
            self.canvas = tk.Canvas(self, bg="#0e1624", highlightthickness=0)
            self.canvas.pack(fill="both", expand=True)
            self.view_yaw = math.radians(35)
            self.zoom = 18.0
            self.drag_x = 0
            self.canvas.bind("<Configure>", lambda _e: self._draw_canvas(self.last_values))
            self.canvas.bind("<ButtonPress-1>", self._drag_start)
            self.canvas.bind("<B1-Motion>", self._drag_move)
            self.canvas.bind("<MouseWheel>", self._zoom)

    def update_scene(self, values: dict) -> None:
        self.last_values = values
        if not self.available:
            self._draw_canvas(values)
            return
        from vtkmodules.vtkFiltersSources import vtkCubeSource, vtkPlaneSource
        from vtkmodules.vtkRenderingCore import vtkActor, vtkPolyDataMapper

        self.renderer.RemoveAllViewProps()

        def actor_for(source, color, opacity=1.0):
            mapper = vtkPolyDataMapper()
            mapper.SetInputConnection(source.GetOutputPort())
            actor = vtkActor()
            actor.SetMapper(mapper)
            actor.GetProperty().SetColor(*color)
            actor.GetProperty().SetOpacity(opacity)
            return actor

        xmin, xmax = float(values.get("Xmin_InterestZone", -5)), float(values.get("Xmax_InterestZone", 5))
        ymin, ymax = float(values.get("Ymin_InterestZone", 0)), float(values.get("Ymax_InterestZone", 1))
        ground = vtkPlaneSource()
        margin = 3.0
        ground.SetOrigin(xmin - margin, ymin - margin, 0)
        ground.SetPoint1(xmax + margin, ymin - margin, 0)
        ground.SetPoint2(xmin - margin, ymax + margin, 0)
        ground_actor = actor_for(ground, (0.16, 0.30, 0.17))
        self.renderer.AddActor(ground_actor)

        crop = vtkPlaneSource()
        crop.SetOrigin(xmin, ymin, 0.015)
        crop.SetPoint1(xmax, ymin, 0.015)
        crop.SetPoint2(xmin, ymax, 0.015)
        crop_actor = actor_for(crop, (0.25, 0.66, 0.30), 0.72)
        crop_actor.GetProperty().SetEdgeVisibility(True)
        crop_actor.GetProperty().SetEdgeColor(0.5, 0.9, 0.5)
        self.renderer.AddActor(crop_actor)

        nx, ny = max(1, int(values.get("NumberOfPanelsX", 1))), max(1, int(values.get("NumberOfPanelsY", 1)))
        bx, by = max(1, int(values.get("NumberOfPVBlocksX", 1))), max(1, int(values.get("NumberOfPVBlocksY", 1)))
        pdx, pdy = float(values.get("RepetitionDistanceOfPanelsX", 2.45)), float(values.get("RepetitionDistanceOfPanelsY", 1.31))
        bdx, bdy = float(values.get("RepetitionDistanceOfPVBlocksX", 4.5)), float(values.get("RepetitionDistanceOfPVBlocksY", 20.0))
        width, length = float(values.get("PanelDimensionX", 2.384)), float(values.get("PanelDimensionY", 1.303))
        thickness = max(0.025, float(values.get("PanelDimensionZ", 0.1)))
        height, tilt, azimuth = float(values.get("Height", 1.22)), float(values.get("TiltY", 20)), float(values.get("CentralAzimut", 90))
        total = nx * ny * bx * by
        stride = max(1, int((total / 600) ** 0.5))
        for ibx in range(bx):
            for iby in range(by):
                for ix in range(0, nx, stride):
                    for iy in range(0, ny, stride):
                        panel = vtkCubeSource()
                        panel.SetXLength(width)
                        panel.SetYLength(length)
                        panel.SetZLength(thickness)
                        actor = actor_for(panel, (0.08, 0.30, 0.58))
                        x = (ibx - (bx - 1) / 2) * bdx + (ix - (nx - 1) / 2) * pdx
                        y = (iby - (by - 1) / 2) * bdy + (iy - (ny - 1) / 2) * pdy
                        actor.SetPosition(x, y, height)
                        actor.RotateY(-tilt)
                        actor.RotateZ(-azimuth)
                        actor.GetProperty().SetSpecular(0.35)
                        actor.GetProperty().SetSpecularPower(18)
                        self.renderer.AddActor(actor)

        if self.initial_camera:
            self.renderer.ResetCamera()
            camera = self.renderer.GetActiveCamera()
            camera.Azimuth(35)
            camera.Elevation(25)
            self.renderer.ResetCameraClippingRange()
            self.initial_camera = False
        self.widget.GetRenderWindow().Render()

    def reset_camera(self) -> None:
        if self.available:
            self.renderer.ResetCamera()
            self.renderer.GetActiveCamera().Azimuth(35)
            self.renderer.GetActiveCamera().Elevation(25)
            self.widget.GetRenderWindow().Render()
        else:
            self.view_yaw = math.radians(35)
            self.zoom = 18.0
            self._draw_canvas(self.last_values)

    def _drag_start(self, event) -> None:
        self.drag_x = event.x

    def _drag_move(self, event) -> None:
        self.view_yaw += (event.x - self.drag_x) * 0.01
        self.drag_x = event.x
        self._draw_canvas(self.last_values)

    def _zoom(self, event) -> None:
        self.zoom = max(3.0, min(80.0, self.zoom * (1.12 if event.delta > 0 else 0.89)))
        self._draw_canvas(self.last_values)

    def _project(self, x: float, y: float, z: float) -> tuple[float, float]:
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        rotated_x = x * math.cos(self.view_yaw) - y * math.sin(self.view_yaw)
        rotated_y = x * math.sin(self.view_yaw) + y * math.cos(self.view_yaw)
        return width / 2 + rotated_x * self.zoom, height * 0.62 - (z + rotated_y * 0.42) * self.zoom

    def _draw_canvas(self, values: dict) -> None:
        if not hasattr(self, "canvas") or not values:
            return
        self.canvas.delete("all")
        xmin, xmax = float(values.get("Xmin_InterestZone", -5)), float(values.get("Xmax_InterestZone", 5))
        ymin, ymax = float(values.get("Ymin_InterestZone", 0)), float(values.get("Ymax_InterestZone", 1))
        ground = [self._project(x, y, 0) for x, y in ((xmin-3, ymin-3), (xmax+3, ymin-3), (xmax+3, ymax+3), (xmin-3, ymax+3))]
        self.canvas.create_polygon(*ground, fill="#233c2c", outline="#4d7655", width=2)
        crop = [self._project(x, y, 0.02) for x, y in ((xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax))]
        self.canvas.create_polygon(*crop, fill="#367f45", outline="#73c982", width=2)
        nx, ny = max(1, int(values.get("NumberOfPanelsX", 1))), max(1, int(values.get("NumberOfPanelsY", 1)))
        bx, by = max(1, int(values.get("NumberOfPVBlocksX", 1))), max(1, int(values.get("NumberOfPVBlocksY", 1)))
        pdx, pdy = float(values.get("RepetitionDistanceOfPanelsX", 2.45)), float(values.get("RepetitionDistanceOfPanelsY", 1.31))
        bdx, bdy = float(values.get("RepetitionDistanceOfPVBlocksX", 4.5)), float(values.get("RepetitionDistanceOfPVBlocksY", 20))
        pw, pl = float(values.get("PanelDimensionX", 2.384)), float(values.get("PanelDimensionY", 1.303))
        height = float(values.get("Height", 1.22))
        tilt, azimuth = math.radians(float(values.get("TiltY", 20))), math.radians(float(values.get("CentralAzimut", 90)))
        panels = []
        stride = max(1, int(((nx * ny * bx * by) / 500) ** 0.5))
        for ibx in range(bx):
            for iby in range(by):
                for ix in range(0, nx, stride):
                    for iy in range(0, ny, stride):
                        cx = (ibx-(bx-1)/2)*bdx + (ix-(nx-1)/2)*pdx
                        cy = (iby-(by-1)/2)*bdy + (iy-(ny-1)/2)*pdy
                        corners = []
                        for lx, ly in ((-pw/2,-pl/2), (pw/2,-pl/2), (pw/2,pl/2), (-pw/2,pl/2)):
                            tx, tz = lx*math.cos(tilt), -lx*math.sin(tilt)
                            x = cx + tx*math.cos(azimuth) - ly*math.sin(azimuth)
                            y = cy + tx*math.sin(azimuth) + ly*math.cos(azimuth)
                            corners.append(self._project(x, y, height+tz))
                        panels.append((sum(p[1] for p in corners)/4, corners))
        for _depth, corners in sorted(panels):
            self.canvas.create_polygon(*corners, fill="#1769aa", outline="#70b7ed", width=1)
        self.canvas.create_text(16, 16, anchor="nw", fill="#b8c7dc", text="Prévia geométrica em tempo real", font=("Segoe UI", 10, "bold"))

class PaseGui(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("PASE Studio - Editor de Simulação Agrivoltaica")
        self.geometry("1480x860")
        self.minsize(1100, 680)
        self.process: subprocess.Popen | None = None
        self.messages: queue.Queue[str] = queue.Queue()
        self.preview_job = None
        self._build()
        self.after(300, self.update_preview)
        self.after(100, self._read_messages)

    def _build(self) -> None:
        header = ttk.Frame(self, padding=(14, 10))
        header.pack(fill="x")
        ttk.Label(header, text="PASE Studio", font=("Segoe UI", 20, "bold")).pack(side="left")
        ttk.Label(header, text="Editor visual de simulação", font=("Segoe UI", 10)).pack(side="left", padx=15)
        self.run_button = ttk.Button(header, text="▶ Salvar e executar", command=self.run)
        self.run_button.pack(side="right")
        ttk.Button(header, text="Abrir resultados", command=self.open_results).pack(side="right", padx=8)
        ttk.Button(header, text="Salvar", command=self.save_all).pack(side="right", padx=4)
        ttk.Button(header, text="Salvar como simulação…", command=self.save_preset).pack(side="right", padx=4)

        workspace = ttk.Panedwindow(self, orient="horizontal")
        workspace.pack(fill="both", expand=True, padx=12)

        scene_panel = ttk.Frame(workspace, width=210, padding=8)
        ttk.Label(scene_panel, text="CENA", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(0, 8))
        self.scene_tree = ttk.Treeview(scene_panel, show="tree", selectmode="browse", height=16)
        root_item = self.scene_tree.insert("", "end", text="🌐 Simulação", open=True)
        icons = ["📍", "☀", "▦", "⚙", "🌱", "🌾", "▰"]
        scene_items = [(f"{icons[index]} {title}", index) for index, title in enumerate(CONFIGS)]
        for label, index in scene_items:
            self.scene_tree.insert(root_item, "end", iid=f"section-{index}", text=label)
        self.scene_tree.pack(fill="both", expand=True)
        self.scene_tree.bind("<<TreeviewSelect>>", self._select_scene_item)
        ttk.Separator(scene_panel).pack(fill="x", pady=10)
        ttk.Label(scene_panel, text="SIMULAÇÕES SALVAS", font=("Segoe UI", 9, "bold")).pack(anchor="w")
        self.preset_name = tk.StringVar()
        self.preset_combo = ttk.Combobox(scene_panel, textvariable=self.preset_name, state="readonly")
        self.preset_combo.pack(fill="x", pady=6)
        ttk.Button(scene_panel, text="Carregar simulação", command=self.load_preset).pack(fill="x")
        ttk.Button(scene_panel, text="Excluir simulação", command=self.delete_preset).pack(fill="x", pady=4)
        workspace.add(scene_panel, weight=0)

        viewport_panel = ttk.Frame(workspace)
        viewport_toolbar = ttk.Frame(viewport_panel, padding=(8, 5))
        viewport_toolbar.pack(fill="x")
        ttk.Label(viewport_toolbar, text="VISUALIZAÇÃO 3D", font=("Segoe UI", 10, "bold")).pack(side="left")
        ttk.Label(viewport_toolbar, text="Arraste para girar • roda para aproximar", foreground="#666").pack(side="left", padx=15)
        ttk.Button(viewport_toolbar, text="Enquadrar cena", command=lambda: self.preview.reset_camera()).pack(side="right")
        self.preview = ScenePreview(viewport_panel)
        self.preview.pack(fill="both", expand=True)
        workspace.add(viewport_panel, weight=1)

        inspector = ttk.Frame(workspace, width=390)
        ttk.Label(inspector, text="INSPETOR DE PROPRIEDADES", font=("Segoe UI", 10, "bold"), padding=(10, 8)).pack(anchor="w")
        self.notebook = ttk.Notebook(inspector)
        self.tabs: list[ConfigTab] = []
        for title, path in CONFIGS.items():
            tab = ConfigTab(self.notebook, path, self.schedule_preview)
            self.tabs.append(tab)
            self.notebook.add(tab, text=title)
        self.notebook.pack(fill="both", expand=True)
        workspace.add(inspector, weight=0)

        output_frame = ttk.LabelFrame(self, text="Andamento da simulação", padding=6)
        output_frame.pack(fill="x", padx=12, pady=(8, 5))
        self.progress = ttk.Progressbar(output_frame, mode="indeterminate")
        self.progress.pack(fill="x", pady=(0, 5))
        self.log = tk.Text(output_frame, height=5, state="disabled", bg="#111827", fg="#e5e7eb", insertbackground="white")
        self.log.pack(fill="both", expand=True)
        self.status = tk.StringVar(value="Pronto para configurar.")
        ttk.Label(self, textvariable=self.status, padding=(14, 2, 14, 8)).pack(fill="x")
        self.refresh_presets()

    def _select_scene_item(self, _event=None) -> None:
        selected = self.scene_tree.selection()
        if selected and selected[0].startswith("section-"):
            self.notebook.select(int(selected[0].split("-")[1]))

    def current_values(self) -> dict:
        values = {}
        for tab in self.tabs:
            values.update(tab.values())
        return values

    def schedule_preview(self) -> None:
        if self.preview_job:
            self.after_cancel(self.preview_job)
        self.preview_job = self.after(180, self.update_preview)

    def update_preview(self) -> None:
        self.preview_job = None
        self.preview.update_scene(self.current_values())

    def refresh_presets(self) -> None:
        PRESETS.mkdir(exist_ok=True)
        names = [path.stem for path in sorted(PRESETS.glob("*.json"))]
        self.preset_combo.configure(values=names)
        if names and self.preset_name.get() not in names:
            self.preset_name.set(names[0])

    def save_preset(self) -> None:
        name = simpledialog.askstring("Salvar simulação", "Nome da simulação:", parent=self)
        if not name:
            return
        safe_name = "".join(char for char in name.strip() if char.isalnum() or char in " -_")
        if not safe_name:
            messagebox.showerror("PASE", "Informe um nome válido.", parent=self)
            return
        payload = {title: tab.values() for (title, _path), tab in zip(CONFIGS.items(), self.tabs)}
        (PRESETS / f"{safe_name}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self.refresh_presets()
        self.preset_name.set(safe_name)
        self.status.set(f'Simulação "{safe_name}" salva.')

    def load_preset(self) -> None:
        path = PRESETS / f"{self.preset_name.get()}.json"
        if not path.exists():
            return
        payload = json.loads(path.read_text(encoding="utf-8"))
        for (title, _path), tab in zip(CONFIGS.items(), self.tabs):
            for name, value in payload.get(title, {}).items():
                if name in tab.variables:
                    tab.variables[name].set(value)
        self.update_preview()
        self.status.set(f'Simulação "{path.stem}" carregada.')

    def delete_preset(self) -> None:
        path = PRESETS / f"{self.preset_name.get()}.json"
        if path.exists() and messagebox.askyesno("Excluir simulação", f'Excluir "{path.stem}"?', parent=self):
            path.unlink()
            self.preset_name.set("")
            self.refresh_presets()

    def save_all(self, notify: bool = True) -> bool:
        try:
            for tab in self.tabs:
                tab.save()
        except (OSError, ValueError) as exc:
            messagebox.showerror("Configuração inválida", str(exc), parent=self)
            return False
        self.status.set("Configurações salvas.")
        if notify:
            messagebox.showinfo("PASE", "Configurações salvas com sucesso.", parent=self)
        return True

    def run(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("PASE", "Já existe uma simulação em andamento.", parent=self)
            return
        if not self.save_all(notify=False):
            return
        self.run_button.configure(state="disabled")
        self.progress.start(12)
        self.status.set("Simulação em andamento...")
        self._append("Iniciando a simulação...\n")
        threading.Thread(target=self._worker, daemon=True).start()

    def _worker(self) -> None:
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        self.process = subprocess.Popen(
            [sys.executable, "example.py"], cwd=ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
            creationflags=creationflags,
        )
        assert self.process.stdout is not None
        for line in self.process.stdout:
            self.messages.put(line)
        code = self.process.wait()
        self.messages.put(f"\0{code}")

    def _read_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message.startswith("\0"):
                    code = int(message[1:])
                    self.progress.stop()
                    self.run_button.configure(state="normal")
                    if code == 0:
                        self.status.set("Simulação concluída com sucesso.")
                        messagebox.showinfo("PASE", "Simulação concluída com sucesso.", parent=self)
                    else:
                        self.status.set("A simulação terminou com erro. Consulte o painel de andamento.")
                        messagebox.showerror("PASE", "A simulação terminou com erro.", parent=self)
                else:
                    self._append(message)
        except queue.Empty:
            pass
        self.after(100, self._read_messages)

    def _append(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text)
        self.log.see("end")
        self.log.configure(state="disabled")

    def open_results(self) -> None:
        output = ROOT / "OUTPUTS"
        output.mkdir(exist_ok=True)
        os.startfile(output)


if __name__ == "__main__":
    PaseGui().mainloop()
