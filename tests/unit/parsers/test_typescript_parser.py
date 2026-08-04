from unittest.mock import MagicMock

import pytest

from codegraphcontext.tools.languages.typescript import TypescriptTreeSitterParser, pre_scan_typescript
from codegraphcontext.tools.tree_sitter_parser import TreeSitterParser
from codegraphcontext.utils.tree_sitter_manager import get_tree_sitter_manager


@pytest.fixture(scope="module")
def ts_parser():
    manager = get_tree_sitter_manager()
    if not manager.is_language_available("typescript"):
        pytest.skip("TypeScript tree-sitter grammar is not available in this environment")

    wrapper = MagicMock()
    wrapper.language_name = "typescript"
    wrapper.language = manager.get_language_safe("typescript")
    wrapper.parser = manager.create_parser("typescript")
    return TypescriptTreeSitterParser(wrapper)


def test_tree_sitter_dispatches_typescript_parser():
    parser = TreeSitterParser("typescript")
    assert isinstance(parser.language_specific_parser, TypescriptTreeSitterParser)


def test_parse_typescript_functions_and_classes(ts_parser, temp_test_dir):
    code = """
import { readFile } from 'fs';
import path from 'path';

interface Animal {
    name: string;
    speak(): void;
}

class Dog implements Animal {
    name: string;

    constructor(name: string) {
        this.name = name;
    }

    speak(): void {
        console.log(this.name);
    }
}

function greet(name: string): string {
    return 'Hello, ' + name;
}

const add = (a: number, b: number): number => {
    return a + b;
};
"""
    f = temp_test_dir / "sample.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    assert result["lang"] == "typescript"

    function_names = {fn["name"] for fn in result["functions"]}
    assert "greet" in function_names
    assert "add" in function_names

    class_names = {cls["name"] for cls in result["classes"]}
    assert "Dog" in class_names

    import_names = {imp["name"] for imp in result["imports"]}
    assert "fs" in import_names or "path" in import_names


def test_parse_typescript_function_calls(ts_parser, temp_test_dir):
    code = """
function main(): void {
    const result = greet('world');
    console.log(result);
}

function greet(name: string): string {
    return 'Hello, ' + name;
}
"""
    f = temp_test_dir / "calls.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    call_names = {call["name"] for call in result["function_calls"]}
    assert "greet" in call_names or "log" in call_names


def test_pre_scan_typescript_indexes_functions(temp_test_dir):
    code = """
function helper(): void {}

function main(): void {
    helper();
}
"""
    f = temp_test_dir / "scanner.ts"
    f.write_text(code)

    manager = get_tree_sitter_manager()
    wrapper = MagicMock()
    wrapper.language_name = "typescript"
    wrapper.language = manager.get_language_safe("typescript")
    wrapper.parser = manager.create_parser("typescript")

    imports_map = pre_scan_typescript([f], wrapper)

    assert "helper" in imports_map or "main" in imports_map


def _context_for(result, call_name):
    """Return the caller context tuple recorded for a call by name."""
    calls = [c for c in result["function_calls"] if c["name"] == call_name]
    assert calls, f"call {call_name!r} not found in {[c['name'] for c in result['function_calls']]}"
    return calls[0]["context"]


def test_call_inside_anonymous_callback_is_attributed_to_named_function(ts_parser, temp_test_dir):
    """A call in an anonymous callback belongs to the nearest NAMED enclosing function.

    Regression test for #1570: the walk used to stop at the arrow function, return a
    nameless caller, and the CALLS edge was attributed to the file instead of to
    `requestTimeout`, so `find_callers` missed the call site.
    """
    code = """export const requestTimeout = (req, res, next) => {
    setTimeout(() => {
        customErrorHandler(req, res);
    }, 1000);
};

function customErrorHandler(...args: unknown[]) {}
"""
    f = temp_test_dir / "callback.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    name, _, line = _context_for(result, "customErrorHandler")
    assert name == "requestTimeout"
    assert line == 1


def test_call_inside_deeply_nested_callbacks_is_attributed_to_named_function(ts_parser, temp_test_dir):
    code = """function outer(): void {
    p.then(() => {
        q.then(() => {
            target();
        });
    });
}
"""
    f = temp_test_dir / "nested.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    assert _context_for(result, "target")[0] == "outer"


def test_call_inside_callback_in_method_is_attributed_to_method(ts_parser, temp_test_dir):
    code = """class Svc {
    run(): void {
        items.forEach(x => { helper(x); });
    }
}
"""
    f = temp_test_dir / "method_callback.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    name, context_type, _ = _context_for(result, "helper")
    assert name == "run"
    assert context_type == "method_definition"


def test_class_property_arrow_is_a_function_and_owns_its_calls(ts_parser, temp_test_dir):
    """`handler = () => {…}` is a named function; calls inside belong to it, not to the class."""
    code = """class Svc {
    handler = () => { helper(); };
    nested = () => { setTimeout(() => { helper(); }, 1); };
}

function helper(): void {}
"""
    f = temp_test_dir / "class_field.ts"
    f.write_text(code)

    result = ts_parser.parse(f)

    fns = {fn["name"]: fn for fn in result["functions"]}
    assert fns["handler"]["line_number"] == 2
    assert fns["handler"]["class_context"] == "Svc"
    assert fns["nested"]["class_context"] == "Svc"

    contexts = [c["context"][0] for c in result["function_calls"] if c["name"] == "helper"]
    assert contexts == ["handler", "nested"]
