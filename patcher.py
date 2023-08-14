from functools import wraps
import ast
import bpy
import inspect
import sys
import textwrap
import traceback

from . import prefs
from .log import logd

class PanelPatcher(ast.NodeTransformer):
    """
    Allows patching Blender native UI panels. If patching fails and fallback_func is provided,
    it will be appended to the panel's draw functions in the usual way.
    Enabling debug mode will dump and copy a lot of useful text to the clipboard.
    Example usage overriding an operator button with our own custom version:

    class ShapeKeyPanelPatcher(PanelPatcher):
        panel_type = bpy.types.DATA_PT_shape_keys
        def visit_Call(self, node):
            super().generic_visit(node)  # Remember to call to keep visiting children
            if node.func.attr == "operator":
                for arg in node.args:
                    if arg.value == "object.shape_key_clear":
                        arg.value = "gret.shape_key_clear"
            return node  # Can return a list of nodes if visiting an expression
    patcher = ShapeKeyPanelPatcher()
    patcher.patch(debug=True)
    patcher.unpatch()
    """

    saved_draw_func = None
    fallback_func = None
    panel_type = None

    def patch(self, debug=False):
        if self.panel_type is None:
            # Fail silently if the panel doesn't exist, it seems this is a thing that can happen
            return

        if not self.panel_type.is_extended():
            # Force panel to be extended to avoid issues. This overrides draw() and adds _draw_funcs
            self.panel_type.append(_dummy)
            self.panel_type.remove(_dummy)

        if prefs.use_panel_patcher:
            saved_draw_func = self.panel_type.draw._draw_funcs[0]
            new_draw_func = patch_module(saved_draw_func, self, debug=debug)
        else:
            saved_draw_func, new_draw_func = None, None

        if new_draw_func:
            self.saved_draw_func = saved_draw_func
            self.panel_type.draw._draw_funcs[0] = new_draw_func
        elif self.fallback_func:
            self.panel_type.append(self.fallback_func)

    def unpatch(self):
        if self.panel_type is None:
            return

        if self.saved_draw_func:
            self.panel_type.draw._draw_funcs[0] = self.saved_draw_func
        elif self.fallback_func:
            self.panel_type.remove(self.fallback_func)

class FunctionPatcher(dict):
    """
    Allows patching functionality in foreign modules.
    Use the patcher object as a dictionary in order to pass additional data to the override function.
    Example usage:

    def cos_wrapper(cos, x, **kwargs):
        if kwargs.pop('degrees', False):
            x *= math.pi / 180
        return cos(x)
    import math
    with FunctionPatcher('math', 'cos', cos_wrapper) as patcher:
        patcher['degrees'] = True
        print(math.cos(180.0))
    """

    def __init__(self, module_names, function_name, func, use_wrapper=True):
        self.module_names = module_names
        self.function_name = function_name
        self.func = func

    def get_module(self, module_names):
        if isinstance(module_names, str):
            module_names = (module_names,)
        if isinstance(module_names, (tuple, list)):
            import importlib
            for module_name in module_names:
                try:
                    return importlib.import_module(module_name)
                except ImportError:
                    pass
        return None

    def get_func(self, module, function_name):
        for part in function_name.split("."):
            module = getattr(module, part, None)
        return module

    def __enter__(self):
        module = self.get_module(self.module_names)
        if module:
            base_func = self.get_func(module, self.function_name)
            if base_func:
                @wraps(base_func)
                def wrapper(*args, **kwargs):
                    kwargs.update(self)
                    return self.func(base_func, *args, **kwargs)
                setattr(module, self.function_name, wrapper)
            else:
                logd(f"Couldn't patch {module.__name__}.{self.function_name}, function not found")
        else:
            module_name = self.module_names if isinstance(self.module_names, str) else self.module_names[0]
            logd(f"Couldn't patch {module_name}.{self.function_name}, module not found")
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        module = self.get_module(self.module_names)
        if module:
            wrapper = self.get_func(module, self.function_name)
            if wrapper and hasattr(wrapper, '__wrapped__'):
                setattr(module, self.function_name, wrapper.__wrapped__)

def patch_module(module, visitor, debug=False):
    if debug:
        print(f"{'Patching' if visitor else 'Dumping'} {module}")

    debug_text = ""
    def printd(*args):
        nonlocal debug_text
        for arg in args:
            debug_text += "-" * 80 + "\n"
            debug_text += str(arg) + "\n"

    try:
        source = textwrap.dedent(inspect.getsource(module))
        tree = ast.parse(source)
    except OSError:
        return module

    if debug:
        printd("BEGIN SOURCE", source)
        printd("BEGIN AST DUMP", ast.dump(tree, include_attributes=True, indent=2))
        print(f"Copied source and AST dump of {module} to clipboard")

    new_tree = None
    if visitor:
        try:
            new_tree = ast.fix_missing_locations(visitor.visit(tree))
            if debug:
                try:
                    import astunparse
                    printd("BEGIN OUTPUT SOURCE", astunparse.unparse(tree))
                except ModuleNotFoundError:
                    pass
                printd("BEGIN OUTPUT AST DUMP", ast.dump(tree, include_attributes=True, indent=2))
                print(f"Copied output of patching {module} to clipboard")
        except:
            if debug:
                print(f"Copied visit exception to clipboard")
                printd("VISIT EXCEPTION", traceback.format_exc())

    new_code = None
    if new_tree:
        try:
            new_code = compile(new_tree, filename="<ast>", mode='exec')
        except:
            if debug:
                print(f"Copied compile exception to clipboard")
                printd("COMPILE EXCEPTION", traceback.format_exc())

    new_module = None
    if new_code:
        try:
            new_locals = {}
            exec(new_code, {}, new_locals)
            new_module = new_locals[module.__name__]
        except:
            if debug:
                print(f"Copied execution exception to clipboard")
                printd("EXEC EXCEPTION", traceback.format_exc())

    if debug_text:
        bpy.context.window_manager.clipboard = debug_text

    return new_module

def _dummy(self, context): pass
