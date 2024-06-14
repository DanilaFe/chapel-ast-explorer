from textual import on
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Header, Tree, RichLog, Input, Log
from rich.syntax import Syntax
from rich.segment import Segment, Segments
from rich.console import Console, ConsoleOptions, RenderResult
from rich.padding import Padding
from rich.style import Style
import chapel
import sys
import ast
import typing

context = chapel.Context()
asts = context.parse(sys.argv[1])
text = context.get_file_text(sys.argv[1])
text_lines = text.splitlines()
max_line_length = max(len(line) for line in text_lines)

# Implementation of exec_with_return from: https://stackoverflow.com/a/76636602
def exec_with_return(code: str, globals: dict, locals: dict) -> typing.Any | None:
    a = ast.parse(code)
    last_expression = None
    if a.body:
        if isinstance(a_last := a.body[-1], ast.Expr):
            last_expression = ast.unparse(a.body.pop())
        elif isinstance(a_last, ast.Assign):
            last_expression = ast.unparse(a_last.targets[0])
        elif isinstance(a_last, (ast.AnnAssign, ast.AugAssign)):
            last_expression = ast.unparse(a_last.target)
    exec(ast.unparse(a), globals, locals)
    if last_expression:
        return eval(last_expression, globals, locals)

class SyntaxWithUnderline(Syntax):
    def __init__(self, location, *args, **kwargs):
        super().__init__(*args, **kwargs)
        (self.start_line, self.start_column), (self.end_line, self.end_column) = location

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        syntax_segments = self._get_syntax(console, options)

        # Pre-process the syntax segments to track the current line and column,
        # and to underline the specified range.
        new_segments = []
        for (line, seg_line) in enumerate(Segment.split_lines(syntax_segments)):
            if line > 0:
                new_segments.append(Segment("\n"))

            if line < self.start_line or line > self.end_line:
                continue

            # What part of this line region is highlighted?
            start_pos = 0 if line > self.start_line else self.start_column
            end_pos = sum(len(s) for s in seg_line) if line < self.end_line else self.end_column

            col = 0
            for segment in seg_line:
                next_col = col + len(segment.text)

                # Skip if we're out of bounds.
                if line == self.start_line and next_col <= self.start_column:
                    new_segments.append(segment)
                    col = next_col
                    continue
                if line == self.end_line and col >= self.end_column:
                    new_segments.append(segment)
                    col = next_col
                    continue

                # Adjust desired sub-range.
                my_start_pos = max(start_pos - col, 0)
                my_end_pos = min(end_pos - col, len(segment.text))

                # Pieces before and after are unchanged.
                new_style = segment.style.copy() if segment.style else Style()
                new_style += Style(underline=True)
                new_segments.append(Segment(segment.text[0:my_start_pos], segment.style))
                new_segments.append(Segment(segment.text[my_start_pos:my_end_pos], new_style))
                new_segments.append(Segment(segment.text[my_end_pos:], segment.style))

                col = next_col

        # Code from parent, as before.
        segments = Segments(new_segments)
        if self.padding:
            yield Padding(
                segments, style=self._theme.get_background_style(), pad=self.padding
            )
        else:
            yield segments

class AstExplorer(App):
    def __init__(self):
        super().__init__()
        self.selected_ast = None
        self.history = []
        self.env = {}
        self.repl_globals = globals().copy()
        self.tree_nodes_for_ast = {}

        self.repl_globals['select'] = lambda node: self.select_node(node)

    def compose(self) -> ComposeResult:
        yield Header()
        self.mytree = Tree("Chapel AST")
        for ast in asts:
            self.build_ast(ast, self.mytree.root)

        log = RichLog(highlight=True, markup=False, min_width=max_line_length + 1)
        log.auto_scroll = False
        yield Horizontal(self.mytree, log)
        yield Log()
        yield Input()

    def on_ready(self):
        text_log = self.query_one(RichLog)
        text_log.write(Syntax(text, "chapel", indent_guides=True))

        repl_log = self.query_one(Log)
        self.repl_globals['print'] = lambda *args: repl_log.write_line(" ".join(map(str, args)))

    def on_tree_node_selected(self, node_selected):
        ast = node_selected.node.data
        if ast is None:
            return
        self.show_ast(ast)

    def show_ast(self, ast):
        self.selected_ast = ast

        text_log = self.query_one(RichLog)

        loc = ast.location()
        first_line, first_col = loc.start()
        last_line, last_col = loc.end()
        if first_line == -1:
            return

        # Underline location, relative to first line and column.
        underline_loc = ((0, first_col - 1), (last_line - first_line, last_col - 1))

        lines_before = text_lines[:first_line-1]
        lines_selected = text_lines[first_line-1:last_line]
        lines_after = text_lines[last_line:]
        text_log.clear()
        text_log.write(Syntax("\n".join(lines_before), "text", indent_guides=True))
        text_log.write(SyntaxWithUnderline(underline_loc, "\n".join(lines_selected), "chapel", indent_guides=True))
        text_log.write(Syntax("\n".join(lines_after), "text", indent_guides=True))

        text_log.scroll_to(x = 0, y = max(0, first_line - 1. - 5))

    @on(Input.Submitted)
    def on_input_submitted(self, changed: Input.Submitted):
        self.env["current_node"] = self.selected_ast
        for (i, val) in enumerate(self.history):
            self.env[f"_{i}"] = val

        command = changed.value
        log = self.query_one(Log)
        log.write_line(f"> {command}")
        try:
            val = exec_with_return(command, self.repl_globals, self.env)
            if val is not None:
                log.write_line(f"_{len(self.history)} = {str(val)}")
                self.history.append(val)
        except Exception as e:
            log.write_line(str(e))
        changed.input.clear()

    def build_ast(self, ast, add_to):
        label = ast.tag()
        children = list(ast)
        my_node = add_to.add(label, data=ast) if len(children) > 0 else add_to.add_leaf(label, data=ast)
        self.tree_nodes_for_ast[ast.unique_id()] = my_node

        for child in children:
            self.build_ast(child, my_node)

    def select_node(self, ast):
        if self.tree is None:
            return None
        tree_node = self.tree_nodes_for_ast[ast.unique_id()]
        self.mytree.select_node(tree_node)
        self.show_ast(ast)

if __name__ == "__main__":
    app = AstExplorer()
    app.run()