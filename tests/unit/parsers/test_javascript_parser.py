from unittest.mock import MagicMock

import pytest

from codegraphcontext.tools.languages.javascript import JavascriptTreeSitterParser, pre_scan_javascript
from codegraphcontext.tools.tree_sitter_parser import TreeSitterParser
from codegraphcontext.utils.tree_sitter_manager import get_tree_sitter_manager


@pytest.fixture(scope="module")
def js_parser():
    manager = get_tree_sitter_manager()
    if not manager.is_language_available("javascript"):
        pytest.skip("JavaScript tree-sitter grammar is not available in this environment")

    wrapper = MagicMock()
    wrapper.language_name = "javascript"
    wrapper.language = manager.get_language_safe("javascript")
    wrapper.parser = manager.create_parser("javascript")
    return JavascriptTreeSitterParser(wrapper)


def test_tree_sitter_dispatches_javascript_parser():
    parser = TreeSitterParser("javascript")
    assert isinstance(parser.language_specific_parser, JavascriptTreeSitterParser)


def test_parse_javascript_functions_and_classes(js_parser, temp_test_dir):
    code = """
import { readFile } from 'fs';
import path from 'path';

class Animal {
    constructor(name) {
        this.name = name;
    }

    speak() {
        console.log(this.name);
    }
}

function greet(name) {
    return 'Hello, ' + name;
}

const add = (a, b) => {
    return a + b;
};
"""
    f = temp_test_dir / "sample.js"
    f.write_text(code)

    result = js_parser.parse(f)

    assert result["lang"] == "javascript"

    function_names = {fn["name"] for fn in result["functions"]}
    assert "greet" in function_names
    assert "add" in function_names

    class_names = {cls["name"] for cls in result["classes"]}
    assert "Animal" in class_names

    import_names = {imp["name"] for imp in result["imports"]}
    assert "fs" in import_names or "path" in import_names


def test_parse_javascript_cyclomatic_complexity(js_parser, temp_test_dir):
    code = """
function simple() {
    return 1;
}

function complex(x) {
    if (x > 0) {
        for (let i = 0; i < x; i++) {
            if (i % 2 === 0 && x > 5) {
                console.log(i);
            }
        }
    } else if (x < 0) {
        return -1;
    }
    return 0;
}
"""
    f = temp_test_dir / "complexity.js"
    f.write_text(code)

    result = js_parser.parse(f)

    functions_by_name = {fn["name"]: fn for fn in result["functions"]}

    assert "cyclomatic_complexity" in functions_by_name["simple"]
    assert functions_by_name["simple"]["cyclomatic_complexity"] == 1

    assert functions_by_name["complex"]["cyclomatic_complexity"] > 1


def test_parse_javascript_function_calls(js_parser, temp_test_dir):
    code = """
function main() {
    const result = greet('world');
    console.log(result);
}

function greet(name) {
    return 'Hello, ' + name;
}
"""
    f = temp_test_dir / "calls.js"
    f.write_text(code)

    result = js_parser.parse(f)

    call_names = {call["name"] for call in result["function_calls"]}
    assert "greet" in call_names or "log" in call_names


def test_pre_scan_javascript_indexes_functions(temp_test_dir):
    code = """
function helper() {}

function main() {
    helper();
}
"""
    f = temp_test_dir / "scanner.js"
    f.write_text(code)

    manager = get_tree_sitter_manager()
    wrapper = MagicMock()
    wrapper.language_name = "javascript"
    wrapper.language = manager.get_language_safe("javascript")
    wrapper.parser = manager.create_parser("javascript")

    imports_map = pre_scan_javascript([f], wrapper)

    assert "helper" in imports_map or "main" in imports_map

def _js_context_for(result, call_name):
    calls = [c for c in result["function_calls"] if c["name"] == call_name]
    assert calls, f"call {call_name!r} not found in {[c['name'] for c in result['function_calls']]}"
    return calls[0]["context"]


def test_call_inside_anonymous_callback_is_attributed_to_named_function(js_parser, temp_test_dir):
    """Regression test for #1570 (JavaScript side)."""
    code = """const requestTimeout = (req, res, next) => {
    setTimeout(() => {
        customErrorHandler(req, res);
    }, 1000);
};

function customErrorHandler() {}
"""
    f = temp_test_dir / "callback.js"
    f.write_text(code)

    result = js_parser.parse(f)

    name, _, line = _js_context_for(result, "customErrorHandler")
    assert name == "requestTimeout"
    assert line == 1


def test_class_property_arrow_is_a_function_and_owns_its_calls(js_parser, temp_test_dir):
    code = """class Svc {
    handler = () => { helper(); };
    boot = function () { helper(); };
}

function helper() {}
"""
    f = temp_test_dir / "class_field.js"
    f.write_text(code)

    result = js_parser.parse(f)

    fns = {fn["name"]: fn for fn in result["functions"]}
    assert fns["handler"]["line_number"] == 2
    assert fns["handler"]["class_context"] == "Svc"

    contexts = [c["context"][0] for c in result["function_calls"] if c["name"] == "helper"]
    assert contexts == ["handler", "boot"]
