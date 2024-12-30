import sys, os
from pxr import Usd, UsdGeom, Sdf, UsdLux, Gf, Tf
from PySide2.QtWidgets import QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton, QCheckBox, QListWidget, QGroupBox, QMessageBox, QTreeWidget, QTreeWidgetItem, QTableWidget, QTableWidgetItem, QSplitter, QFileDialog, QSizePolicy, QAbstractButton, QBoxLayout, QDialog, QComboBox, QGridLayout
from PySide2.QtCore import Qt, QPoint, Signal, QEvent
from PySide2.QtGui import QMouseEvent, QDoubleValidator
import qdarktheme
from dataclasses import dataclass
from typing import List, Any, Optional, Callable, cast, Tuple, Dict
import re

# Backport of 'dataclasses' for Python 3.6 (can import it normally)
# $ pip install dataclasses
# https://docs.omniverse.nvidia.com/kit/docs/pxr-usd-api/latest/pxr.html

# *************************** Main window and dataclasses for UI state *****************************

@dataclass(frozen=True)
class Attribute:
    path: str
    docs: str
    name: str
    type: str
    value: Any  # Use Any if the value can be of different types

@dataclass(frozen=True)
class Relationship:
    path: str
    docs: str
    name: str

@dataclass(frozen=True)
class PrimInfo:
    type: str
    path: str
    name: str
    attributes: List[Attribute]
    relationships: List[Relationship]

class MainWindow(QWidget):
    usd_stage: Optional[Any] = None
    usd_prim_list: List[PrimInfo]
    path: str

    # ************* Handlers for opening/creating/saving the USD stage (.usda files) *************

    def on_create(self, add_example_primitives_flags: List[bool]):
        if self.usd_stage:
            path = "<unknown path>"
            try: 
                path = self.usd_stage.GetRootLayer().identifier;
                self.usd_stage.Save()
            except Exception as error: 
                show_msg_box(f'Failed to save stage: {path}', str(error), QMessageBox.Close)
            return
        options = QFileDialog.Options()
        path, _ = QFileDialog.getSaveFileName(self, 'Create New Stage', self.stage_path_label.text(), 'USD Files (*.usda);;All Files (*)', options=options)
        if path:
            try:
                self.path = path
                self.usd_stage = create_example_stage(path, *add_example_primitives_flags)
                
                self.usd_stage, self.usd_prim_list = open_and_read_usda(path)
                self.redraw_treeview()
                self.path = path
                # self.stage_path_group.setTitle(path)
                self.create_or_save_button.setText('Save')
                self.stage_path_label.setText(path)
                self.open_button.hide()
                self.examples_primitive_group.hide()
                self.splitter.setEnabled(True)
            except Exception as error:
                show_msg_box(f'Failed to create stage: {path}', str(error), QMessageBox.Close)
        return
    def on_open(self):
        options = QFileDialog.Options()
        path, _ = QFileDialog.getOpenFileName(self, 'Open Stage', '', 'USD Files (*.usda);;All Files (*)', options=options)
        if path:
            try:
                self.open_stage_and_update_ui(path)
            except Exception as error:
                show_msg_box(f'Failed to open stage: {path}', str(error), QMessageBox.Close)
        return
    def open_stage_and_update_ui(self, path: str):
        self.usd_stage, self.usd_prim_list = open_and_read_usda(path)
        self.redraw_treeview()
        self.path = path
        # self.stage_path_group.setTitle(path)
        self.stage_path_label.setText(path)
        self.create_or_save_button.setText('Save')
        self.open_button.hide()
        self.examples_primitive_group.hide()
        self.splitter.setEnabled(True)

    # ************* Tree of prims handlers *************

    def redraw_treeview(self):
        # :PrimHierarchyTraversal
        # @ErrorProne(dsk): Because 'self.usd_prim_list' is a depth-first array and QTreeWidget is a tree-like structure, I use recursion to help me construct the latter.
        # Potentially hierarchy in USD can be quite deep, and recursion in Python is... well...
        # Ideally I should do something else, especially because I count forward slashes to determine the "depth" of a prim.
        #                                                                          dsk -- 27 dec 2024
        def redraw_treeview_internal(expect_n_slashes: int, parent_widget: QTreeWidgetItem, prim_index: int) -> int:
            node = parent_widget
            while prim_index < len(self.usd_prim_list):
                prim: PrimInfo = self.usd_prim_list[prim_index]
                n_slashes = prim.path.count('/')
                if n_slashes < expect_n_slashes: break # Return from the recursion to construct widget in one of the ancestors. Maybe it is possible to create QTreeWidgetItem first, and then add it to a correct parent later (but then I would need to pass it around).
                if n_slashes > expect_n_slashes: prim_index = redraw_treeview_internal(expect_n_slashes+1, node, prim_index)
                else:
                    node = QTreeWidgetItem(parent_widget, [prim.name, prim.type, 'button'])
                    node.setData(0, Qt.UserRole, prim)
                    prim_index += 1
            if expect_n_slashes <= 2: node.setExpanded(True) # Expand up to the second (depth-wise) prim.
            return prim_index

        self.hierarchy_list.clear()
        root = QTreeWidgetItem(self.hierarchy_list, ['/', ''])
        root.setExpanded(True)
        redraw_treeview_internal(1, root, 0)
    def on_tree_clicked(self, item: QTreeWidgetItem, column: int):
        prim_info: Optional[PrimInfo] = item.data(0, Qt.UserRole)
        if prim_info is None: return # I do not add info for the '/' prim (I believe it's called pseudo-root).
        self.table.setRowCount(len(prim_info.attributes))
        for row, attr in enumerate(prim_info.attributes):
            self.table.setItem(row, 0, QTableWidgetItem(attr.type))
            self.table.setItem(row, 1, QTableWidgetItem(attr.name))
            self.table.setItem(row, 2, QTableWidgetItem("" if attr.value is None else str(attr.value)))
    def on_current_item_changed(self, item: QTreeWidgetItem, prev: QTreeWidgetItem):
        if prev: self.on_tree_clicked(item, 0) # Update properties widget.
    def on_add_item(self, item: QTreeWidgetItem):
        assert(item is not None and self.usd_stage is not None)
        prim_info: PrimInfo = item.data(0, Qt.UserRole)

        usd_prim = self.usd_stage.GetPrimAtPath(prim_info.path)
        prim_index = usd_prim.GetPrimIndex() # pxr.Pcp.PrimIndex
        init_layer_path = prim_index.primStack[len(prim_index.primStack)-1].layer.identifier # Layer with the strongest opinion.
        init_prim_path = str(usd_prim.GetParent().GetPath())

        # layers = [sdf_path.layer.GetDisplayName() for sdf_path in prim_index.primStack]
        # layer = prim_index.primStack[0].layer
        root_layer = self.usd_stage.GetRootLayer()
        # sublayers  = root_layer.GetSubLayerPaths()
        # to_remove = 'sphere.usda'
        # sublayers.remove(to_remove)
        # root_layer.SetSubLayerPaths(sublayers)

        self.dialog = CreateNewPrim_FormWindow(self, self.usd_stage, init_prim_path, init_layer_path)
        self.dialog.exec_()
        res = self.dialog.result()
        if res: # Re-read and re-draw everything.
            path = root_layer.identifier
            self.usd_stage, self.usd_prim_list = open_and_read_usda(path)
            self.redraw_treeview()
            self.path = path
    def on_remove_item(self, item: QTreeWidgetItem):
        assert(item is not None)
        prim: PrimInfo = item.data(0, Qt.UserRole)
        try:
            remove_usda_prim(self.usd_stage, prim)
            item.parent().removeChild(item) # @Correctness(dsk) How do we remove widgets again? Can we something like: `item.widget().deleteLater()`, also there are 'takeAt(index)' and 'takeChild(index)'.
            self.usd_prim_list.remove(prim)
        except Exception as error:
            show_msg_box('Failed to remove prim', f'Failed to remove Prim {prim.path}\n{str(error)}', QMessageBox.Close)

    # ************* UI Initialization and widget layout *************

    def __init__(self):
        super().__init__()
        self.setWindowTitle("USD PySide 2 Test")

        main_layout = QVBoxLayout()
        # ----------------------------------------------
        create_stage_layout = QVBoxLayout()
        path_layout = QHBoxLayout()
        path_layout.setAlignment(Qt.AlignLeft)

        create_stage_layout.addLayout(path_layout)

        self.stage_path_group = QGroupBox('USD Stage')
        self.stage_path_group.setLayout(create_stage_layout)
        self.create_or_save_button = QPushButton('Create New...')
        self.create_or_save_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        path_layout.addWidget(self.create_or_save_button)
        self.stage_path_label = QLineEdit(os.getcwd())
        self.open_button = QPushButton('Open...')
        self.open_button.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.open_button.clicked.connect(self.on_open)
        path_layout.addWidget(self.open_button)
        path_layout.addWidget(self.stage_path_label)
        # ----------------------------------------------
        self.examples_primitive_group = QGroupBox("Shapes")
        examples_primitives_layout = QVBoxLayout()
        cube_checkbox = QCheckBox("Cube")
        sphere_checkbox = QCheckBox("Sphere")
        cone_checkbox = QCheckBox("Cone")
        cylinder_checkbox = QCheckBox("Cylinder")
        examples_primitives_layout.addWidget(cube_checkbox)
        examples_primitives_layout.addWidget(sphere_checkbox)
        examples_primitives_layout.addWidget(cone_checkbox)
        examples_primitives_layout.addWidget(cylinder_checkbox)
        self.examples_primitive_group.setLayout(examples_primitives_layout)
        self.examples_primitive_group.setEnabled(True)
        create_stage_layout.setAlignment(Qt.AlignLeft)
        create_stage_layout.addWidget(self.examples_primitive_group)
        # ----------------------------------------------
        self.create_or_save_button.clicked.connect(lambda: self.on_create([cube_checkbox.isChecked(),sphere_checkbox.isChecked(),cone_checkbox.isChecked(),cylinder_checkbox.isChecked()]))
        # ----------------------------------------------
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)
        self.splitter.setOrientation(Qt.Vertical)
        self.splitter.setEnabled(False)
        # ----------------------------------------------
        self.hierarchy_list = QTreeWidget_WithButtons()
        self.hierarchy_list.setColumnCount(2)
        self.hierarchy_list.setColumnWidth(0, 300)
        self.hierarchy_list.setHeaderLabels(['Name', 'Type'])
        self.hierarchy_list.itemClicked.connect(self.on_tree_clicked)
        self.hierarchy_list.currentItemChanged.connect(self.on_current_item_changed)
        self.hierarchy_list.remove_item_signal.connect(self.on_remove_item) 
        self.hierarchy_list.add_item_signal.connect(self.on_add_item) 
        self.splitter.addWidget(self.hierarchy_list)
        # ----------------------------------------------
        self.table = QTableWidget()
        self.table.setColumnCount(3)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setHorizontalHeaderLabels(['Type', 'Property Name', 'Value'])
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.splitter.addWidget(self.table)
        # ----------------------------------------------
        main_layout.addWidget(self.stage_path_group)
        main_layout.addWidget(self.splitter)
        # ----------------------------------------------
        self.setLayout(main_layout)

# ******************************** Create Prim window (potentially a form) *************************

example_prims = {
    'Sphere': ['radius'],
    'Cube':   ['size'],
    'Cone':   ['radius','height'],
    'Cylinder':['radius','height'],
    'Dome Light':['intensity','color','texture'],
}

def to_float_or_none(it: str):
    try:    return float(it)
    except: return None

def to_vec3f_or_none(it: str):
    try:
        it = it.strip().strip('()')
        numbers = re.findall(r'[-+]?\d*\.\d+|[-+]?\d+', it)
        vec = [float(num) for num in numbers]
        if len(vec) == 3:
            return Gf.Vec3f(vec[0], vec[1], vec[2])
    except: pass
    return None

def usd_make_example_prim(stage: Any, path: str, dst_layer_path: str, options: Dict[str,str]) -> Optional[str]:
    if stage.GetPrimAtPath(path):
        return f'Prim with path "{path}" already exists'

    try:
        layer = Sdf.Layer.FindOrOpen(dst_layer_path)
        if layer is None: 
            layer = Sdf.Layer.CreateNew(dst_layer_path)
            root  = stage.GetRootLayer()
            root.subLayerPaths.append(dst_layer_path)
        stage.SetEditTarget(layer)  

        add_transforms = True
        new_prim = None
        if options['name'] == 'Sphere':
            new_prim = UsdGeom.Sphere.Define(stage, path)
            new_prim.CreateRadiusAttr(to_float_or_none(options['radius']))
        elif options['name'] == 'Cube':
            new_prim = UsdGeom.Cube.Define(stage, path)
            new_prim.CreateSizeAttr(to_float_or_none(options['size']))
        elif options['name'] == 'Cone':
            new_prim = UsdGeom.Cone.Define(stage, path)
            new_prim.CreateHeightAttr(to_float_or_none(options['height']))
            new_prim.CreateRadiusAttr(to_float_or_none(options['radius']))
        elif options['name'] == 'Cylinder':
            new_prim = UsdGeom.Cylinder.Define(stage, path)
            new_prim.CreateHeightAttr(to_float_or_none(options['height']))
            new_prim.CreateRadiusAttr(to_float_or_none(options['radius']))
        elif options['name'] == 'Dome Light':
            new_prim = UsdLux.DomeLight.Define(stage, path)
            new_prim.CreateIntensityAttr(to_float_or_none(options['intensity']))
            new_prim.CreateColorAttr(to_vec3f_or_none(options['color']))
            new_prim.CreateTextureFileAttr(str(options['texture']))
            add_transforms = False
        else:
            return "@Incomplete. Should be able to define prims with custom names (I think) and without names."
        
        if add_transforms:
            UsdGeom.XformCommonAPI(new_prim).SetTranslate((0, 0, 0))
            UsdGeom.XformCommonAPI(new_prim).SetRotate((0, 0, 0))
            UsdGeom.XformCommonAPI(new_prim).SetScale((1, 1, 1))

        return None
    except Exception as error: return str(error)

class CreateNewPrim_FormWindow(QDialog):
    prim_attr_names = [x for x in example_prims]

    def __init__(self, parent: QWidget, usd_stage: Any, init_prim_path: str, init_layer_path: str):
        super(CreateNewPrim_FormWindow, self).__init__(parent)

        self.usd_stage = usd_stage

        self.setWindowTitle("Create Prim")
        self.setFixedSize(400, 400)
        self.setModal(True)

        layout = QVBoxLayout()
        self.setLayout(layout)
        layout.setAlignment(Qt.AlignTop)

        # Paths
        paths_grid = QGridLayout(); layout.addLayout(paths_grid)
        paths_grid.addWidget(QLabel('Path: '), 0, 0)
        self.prim_path = QLineEdit(init_prim_path); paths_grid.addWidget(self.prim_path, 0, 1)
        paths_grid.addWidget(QLabel('Layer: '), 1, 0)
        self.layer_path = QLineEdit(init_layer_path); paths_grid.addWidget(self.layer_path, 1, 1)

        # Prim selection
        self.shape_combo = QComboBox()
        self.shape_combo.addItems(self.prim_attr_names)
        self.shape_combo.currentIndexChanged.connect(self.on_prim_selected)
        layout.addWidget(self.shape_combo)

        # Properties
        properties_group = QGroupBox("Attributes")
        self.properties_grid = QGridLayout()
        properties_group.setLayout(self.properties_grid)
        layout.addWidget(properties_group)
        self.on_prim_selected(0)

        stretchable_widget = QWidget()
        stretchable_widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        layout.addWidget(stretchable_widget)
        
        # OK and Cancel buttons
        button_layout = QHBoxLayout()
        ok_button = QPushButton("OK")
        ok_button.clicked.connect(self.on_ok_clicked)
        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(ok_button)
        button_layout.addWidget(cancel_button)
        button_layout.setAlignment(Qt.AlignRight)
        layout.addLayout(button_layout)

    def on_ok_clicked(self):
        params = {'name':self.prim_attr_names[self.shape_combo.currentIndex()]}
        for i in range(int(self.properties_grid.count()/2)):
            label = self.properties_grid.itemAt(i*2).widget();   assert(isinstance(label, QLabel))
            edit  = self.properties_grid.itemAt(i*2+1).widget(); assert(isinstance(edit, QLineEdit))
            if label.isHidden(): break
            params[label.text()]=edit.text()
        error = usd_make_example_prim(self.usd_stage, self.prim_path.text(), self.layer_path.text(), params)
        if error:
            show_msg_box('Failed to create Prim', f'Cannot create prim:\n{error}', QMessageBox.Close)
        else:
            self.accept()

    def on_prim_selected(self, index: int):
        attribute_names = example_prims[self.prim_attr_names[index]]
        for row, name in enumerate(attribute_names):
            if self.properties_grid.itemAt(row*2) is None:
                self.properties_grid.addWidget(QLabel(name), row, 0)
            else:
                self.properties_grid.itemAt(row*2).widget().setText(name)
            if self.properties_grid.itemAt(row*2+1) is None: 
                self.properties_grid.addWidget(QLineEdit(), row, 1)
            else:                                              
                self.properties_grid.itemAt(row*2+1).widget().setText('')
        for i in range(int(self.properties_grid.count()/2)):
            visible = i < len(attribute_names)
            self.properties_grid.itemAt(i*2).widget().setVisible(visible)
            self.properties_grid.itemAt(i*2+1).widget().setVisible(visible)

# ************************ Tree Widget that holds Prim hierarchy ***********************************

class QTreeWidget_WithButtons(QTreeWidget):
    hovered_item: Optional[QTreeWidgetItem] = None
    remove_item_signal = Signal(QTreeWidgetItem)
    add_item_signal = Signal(QTreeWidgetItem)

    def __init__(self, parent: Optional[QWidget]=None):
        super(QTreeWidget_WithButtons, self).__init__(parent)
        def create_button_widget(text: str, on_clicked_handler: Callable[[],None], layout: QBoxLayout) -> QWidget:
            button_size = 22
            font_size = button_size
            style = """
                QPushButton {
                    padding: 0px 0px 2px 1px; /* @Hardcode(dsk): :ReplaceWithIconButton */
                    text-align: center;
                }
            """
            button_widget = QWidget(self)
            button_widget.setLayout(layout)
            button_widget.hide() # Initially hidden.
            button = QPushButton(text) # :ReplaceWithIconButton
            font = button.font()
            font.setBold(True)
            font.setPixelSize(font_size)
            button.setFont(font)
            button.setFixedSize(button_size, button_size)
            button.setStyleSheet(style)
            layout.addWidget(button)
            button.clicked.connect(on_clicked_handler)
            return button_widget
        self.setMouseTracking(True)
        button_layout = QHBoxLayout()
        button_layout.setContentsMargins(0, 0, 0, 0)
        self.button_add_widget = create_button_widget('+', self.add_item_event, button_layout)
        self.button_remove_widget = create_button_widget('-', self.remove_item_event, button_layout)

    def leaveEvent(self, event: QEvent):
        super(QTreeWidget_WithButtons, self).leaveEvent(event)
        self.button_remove_widget.hide()

    def mouseMoveEvent(self, event: QMouseEvent):
        item = self.itemAt(event.pos())
        if item and self.indexOfTopLevelItem(item) != 0: # Skip then '/' (root) item.
            self.hovered_item = item
            vertical_offset = self.viewportMargins().top() + self.childrenRect().x() # This *should* (may be borked with custom stylesheets though) be an offset from the top of the QTreeWidget to the top of the first QTreeWidgetItem.
            row_rect = self.visualItemRect(item) # Relative to the QTreeWidget viewport (does not include the header, hence the 'vertical_offset').
            button_center = QPoint(int(row_rect.right()-self.button_remove_widget.width()/2), row_rect.center().y()+vertical_offset)
            button_top_left = QPoint(int(button_center.x()-self.button_remove_widget.width()/2), int(button_center.y()-self.button_remove_widget.height()/2))
            self.button_remove_widget.move(button_top_left)
            self.button_remove_widget.show()
        else:
            self.button_remove_widget.hide()
        super(QTreeWidget_WithButtons, self).mouseMoveEvent(event)

    def add_item_event(self):
        self.add_item_signal.emit(self.hovered_item) 
        self.hovered_item = None
        self.button_add_widget.hide()

    def remove_item_event(self):
        self.remove_item_signal.emit(self.hovered_item) 
        self.hovered_item = None
        self.button_remove_widget.hide()

# *********************************** UI Helpers ***************************************************

def show_msg_box(title: str, text: str, buttons: QMessageBox.StandardButtons) -> int:
        msg_box = QMessageBox()
        msg_box.setWindowTitle(title)
        msg_box.setText(text)
        msg_box.setStandardButtons(buttons)
        msg_box.resize(600, 300)
        msg_box.exec_()
        return msg_box.result()

# *********************************** USD Helpers **************************************************

def create_example_stage(path:str, do_cube:bool, do_sphere:bool, do_cone:bool, do_cylinder:bool) -> Any:
    stage = Usd.Stage.CreateNew(path) 
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.y) # @Correctness(dsk): Which axis do we use?
    root  = stage.GetRootLayer()
    # stack = stage.GetLayerStack(includeSessionLayers=False)
    # layer = next((it for it in stack if it.GetDisplayName() == 'cube.usda'), None)
    # assert(layer is None)
    layer = None 
    xform = None
    if do_cube:
        layer = Sdf.Layer.CreateNew('cube.usda')
        root.subLayerPaths.append('cube.usda')
        stage.SetEditTarget(layer)
        xform = UsdGeom.Xform.Define(stage, '/Root')
        cube = UsdGeom.Cube.Define(stage, Sdf.Path('/Root/Cube')) 
        cube.GetSizeAttr().Set(0.3)
        # trans = cube.AddTranslateOp()
        # pivot = cube.AddTranslateOp(UsdGeom.XformOp.PrecisionFloat, 'pivot')
        # rotate = cube.AddRotateXYZOp()
        # pivotinv = cube.AddTranslateOp(UsdGeom.XformOp.PrecisionFloat, 'pivot', True)
        # scale = cube.AddScaleOp()
        # trans.Set(Gf.Vec3d(0,0,0), Usd.TimeCode.Default())
        # pivot.Set(Gf.Vec3f(0,0,0), Usd.TimeCode.Default())
        # scale.Set(Gf.Vec3f(0,0,0), Usd.TimeCode.Default())
        # rotate.Set(Gf.Vec3f(0,0,0), Usd.TimeCode.Default())
        # Seems to do the same thing:
        UsdGeom.XformCommonAPI(cube).SetTranslate((0,0,0))
        UsdGeom.XformCommonAPI(cube).SetRotate((0,0,0))
        UsdGeom.XformCommonAPI(cube).SetScale((0,0,0))
    if do_sphere:
        layer = Sdf.Layer.CreateNew('sphere.usda') 
        root.subLayerPaths.append('sphere.usda')
        stage.SetEditTarget(layer)
        if not xform: xform = UsdGeom.Xform.Define(stage, '/Root')
        sphere = UsdGeom.Sphere.Define(stage, Sdf.Path('/Root/Sphere')) 
        sphere.GetRadiusAttr().Set(0.5)
        UsdGeom.XformCommonAPI(sphere).SetTranslate((1, 0, 0))
        UsdGeom.XformCommonAPI(sphere).SetRotate((0,0,0))
        UsdGeom.XformCommonAPI(sphere).SetScale((0,0,0))
    if do_cone:
        layer = Sdf.Layer.CreateNew('cone.usda') 
        root.subLayerPaths.append('cone.usda')
        stage.SetEditTarget(layer)
        if not xform: xform = UsdGeom.Xform.Define(stage, '/Root')
        cone = UsdGeom.Cone.Define(stage, Sdf.Path('/Root/Cone'))
        cone.GetRadiusAttr().Set(0.5)
        cone.GetHeightAttr().Set(1.0)
        UsdGeom.XformCommonAPI(cone).SetTranslate((0, 1, 0))
        UsdGeom.XformCommonAPI(cone).SetRotate((0,0,0))
        UsdGeom.XformCommonAPI(cone).SetScale((0,0,0))
    if do_cylinder:
        layer = Sdf.Layer.CreateNew('cylinder.usda') 
        root.subLayerPaths.append('cylinder.usda')
        stage.SetEditTarget(layer)
        if not xform: xform = UsdGeom.Xform.Define(stage, '/Root')
        cylinder = UsdGeom.Cylinder.Define(stage, Sdf.Path('/Root/Cylinder'))
        cylinder.GetRadiusAttr().Set(0.5)
        cylinder.GetHeightAttr().Set(1.0)
        UsdGeom.XformCommonAPI(cylinder).SetTranslate((0, 0, 1))
        UsdGeom.XformCommonAPI(cylinder).SetRotate((0,0,0))
        UsdGeom.XformCommonAPI(cylinder).SetScale((0,0,0))
    if not xform: xform = UsdGeom.Xform.Define(stage, '/Root') # Always createa /Root Xform.
    if root.dirty: stage.Save() # Should always be True, though.
    return stage

def open_and_read_usda(path: str) -> tuple[Any, list[PrimInfo]]:
    # :PrimHierarchyTraversal
    # Read and translate all information into a format that is easy to use for the UI right away. I do this for two reasons:
    # 1) I'm new to USD API, and may do something silly and crash the program, but this function is called only once, so it's easy to debug.
    # 2) Not to "pollute" UI layout code with USD "parsing" for the time being, because I'm not sure how all of this would look in the future.
    # But there are problems with the exact implementation (see usage site).
    #                                                                             dsk -- 27 dec 2024
    stage: Any       = Usd.Stage.Open(path) 
    prims: List[Any] = [x for x in stage.Traverse()]
    prim_infos = [
        PrimInfo(
            type=str(prim.GetTypeName()),
            path=str(prim.GetPath()),
            name=str(prim.GetName()),
            attributes=[
                Attribute(
                    path=str(it.GetPath()),
                    docs=str(it.GetDocumentation()),
                    name=str(it.GetName()),
                    type=str(it.GetTypeName()),
                    value=prim.GetAttribute(it.GetName()).Get(),
                ) for it in prim.GetAttributes()
            ],
            relationships=[
                Relationship(
                    path=str(it.GetPath()),
                    docs=str(it.GetDocumentation()),
                    name=str(it.GetName()),
                ) for it in prim.GetRelationships()
            ]
        ) for prim in prims
    ]
    return stage, prim_infos

def remove_usda_prim(stage: Any, prim_info: PrimInfo):
    prim = stage.GetPrimAtPath(prim_info.path)
    prim_index = prim.GetPrimIndex()
    for it in reversed(prim_index.primStack):
        stage.SetEditTarget(it.layer)
        ret = stage.RemovePrim(prim_info.path)
        if not ret: raise Exception('Usd.Stage.RemovePrim() returned False')

if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyleSheet(globals()['qdarktheme'].load_stylesheet()) 
    app.setStyleSheet(qdarktheme.load_stylesheet()) 
    window = MainWindow()
    window.resize(600, 800)
    window.show()
    sys.exit(app.exec_())

