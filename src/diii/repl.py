""" diii """
# pylint: disable=C0103
from abc import ABC, abstractmethod
import asyncio
import logging
import logging.config
import os
import sys

from prompt_toolkit.completion import Completer, PathCompleter, WordCompleter
from prompt_toolkit.eventloop.defaults import use_asyncio_event_loop
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.application import Application
from prompt_toolkit.application.current import get_app
from prompt_toolkit.document import Document
from prompt_toolkit.filters import to_filter
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout.containers import (
    to_container,
    Container, FloatContainer, Float,
    VSplit, HSplit,
    Window, WindowAlign,
)
from prompt_toolkit.layout.layout import Layout
from prompt_toolkit.layout.screen import Char
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import TextArea
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.menus import CompletionsMenu

from diii.iii import Deviceiii as ODeviceiii
from diii.viii import Deviceviii
from diii.server import DiiiServer

# Deviceiii = ODeviceiii
Deviceiii = Deviceviii
# if True:
    

logger = logging.getLogger(__name__)

# monkey patch to fix https://github.com/monome/druid/issues/8
Char.display_mappings['\t'] = '  '

diii_intro = "  q to quit. h for help.\n\n"
diii_help = """
 h            this menu
 u <filename> upload <filename>
 u            upload previous file
 p            print current script
 q            quit
"""

last_script = ''

class DiiiUi:
    def __init__(self, use_theme):
        self.statusbar = Window(
            height=1,
            char='─',
            style='class:line',
            content=FormattedTextControl(text=' diii'),
            align=WindowAlign.RIGHT
        )

        self.content = HSplit([
            Window(),
            self.statusbar,
            Window(height=1),
        ])
        self.container = FloatContainer(
            content=self.content,
            floats=[
                Float(
                    xcursor=True,
                    ycursor=True,
                    content=CompletionsMenu(max_height=16, scroll_offset=1),
                ),
            ],
        )
        self.key_bindings = KeyBindings()

        @self.key_bindings.add('c-c', eager=True)
        @self.key_bindings.add('c-q', eager=True)
        def quit_diii(event):
            event.app.exit()

        if use_theme:
            self.style = Style([
                ('capture-field', '#747369'),
                ('output-field', '#d3d0c8'),
                ('input-field', '#f2f0ec'),
                ('line', '#747369'),
                ('scrollbar.background', 'bg:#000000'),
                ('scrollbar.button', 'bg:#747369'),
            ])
        else:
            self.style = Style([])

        self.layout = Layout(self.container)

        self.app = Application(
            layout=self.layout,
            key_bindings=self.key_bindings,
            style=self.style,
            mouse_support=False,
            full_screen=True,
        )

        self.pages = dict()
        self.current_page = None

    def set_page(self, name):
        if self.current_page is not None:
            self.current_page.unmount()
        try:
            self.current_page = self.pages[name]
        except KeyError:
            pass
        else:
            self.current_page.mount()

    def add_page(self, key, page):
        self.pages[key] = page

class UiPage(ABC):
    def __init__(self, ui):
        self.ui = ui
        self.build_ui()

    @abstractmethod
    def build_ui(self):
        pass

    @abstractmethod
    def arrange_ui(self, container):
        pass

    def output_to_field(self, field, st):
        s = field.text + st.replace('\r', '')
        field.buffer.document = Document(text=s, cursor_position=len(s))

    def mount(self):
        self.arrange_ui(self.ui.content)

    def unmount(self):
        pass


class ReplCompleter(Completer):
    III_COMMANDS = {
        "clear": "Clear the saved script",
        "print": "Print the current script",
        "reboot": "Reboot",
        "upload": "Send a file, store and run it",
    }

    def __init__(self):
        self.path_completer = PathCompleter(
            file_filter=lambda s: os.path.isdir(s) or s.endswith('.lua')
        )

        self.word_completer = WordCompleter(
            words=self.III_COMMANDS.keys(),
            ignore_case=True,
            meta_dict=self.III_COMMANDS,
        )

    def offset_document(self, document, offset):
        move_cursor = len(document.current_line) - offset
        return Document(
            document.current_line[offset:],
            cursor_position=document.cursor_position - offset
        )

    def get_completions(self, document, complete_event):
        line = document.current_line.lstrip()
        offset = len(document.current_line) - len(line)

        if line.startswith('^^'):
            line = line[2:]
            offset += 2
            new_document = self.offset_document(document, offset)
            yield from self.word_completer.get_completions(new_document, complete_event)
        elif line.startswith('r ') or line.startswith('u '):
            line = line[2:]
            offset += 2
            rem = line.lstrip()
            offset += len(line) - len(rem)
            new_document = self.offset_document(document, offset)
            yield from self.path_completer.get_completions(new_document, complete_event)


class DiiiRepl(UiPage):
    def __init__(self, ui, iii):
        self.iii = iii
        self.completer = ReplCompleter()
        super().__init__(ui)

        on_disconnect = lambda exc: self.output('  <device disconnected>\n')
        self.handlers = {
            'connect': [lambda: self.output('  <device connected>\n')],
            'connect_etc': [lambda: self.output('  <etc connected>\n')],
            'connect_err': [on_disconnect],
            'disconnect': [on_disconnect],

            'running': [lambda fname: self.output(f'running {fname}\n')],
            'uploading': [lambda fname: self.output(f'uploading {fname}\n')],

            'iii_event': [self.iii_event],
            'iii_output': [lambda output: self.output(output + '\n')],
        }

    def build_ui(self):
        self.captures = [
            TextArea(style='class:capture-field', height=2),
            TextArea(style='class:capture-field', height=2),
        ]
        self.output_field = TextArea(
            style='class:output-field',
            text=diii_intro,
            scrollbar=True,
        )
        self.output_field.window.right_margins[0].display_arrows = to_filter(False)
        self.input_field = TextArea(
            height=1,
            prompt='> ',
            multiline=False,
            wrap_lines=False,
            style='class:input-field',
            completer=self.completer,
            complete_while_typing=True,
        )
        self.input_field.accept_handler = self.accept

    def arrange_ui(self, container):
        container.children.clear()
        container.children.extend([
            VSplit(self.captures),
            to_container(self.output_field),
            self.ui.statusbar,
            to_container(self.input_field),
        ])

    def mount(self):
        self.iii.replace_handlers(self.handlers)
        super().mount()
        pgup = lambda evt: self.pageup(evt, self.output_field)
        pgdn = lambda evt: self.pagedown(evt, self.output_field)
        self.ui.key_bindings.add('pageup')(pgup)
        self.ui.key_bindings.add('escape', 'v')(pgup)
        self.ui.key_bindings.add('pagedown')(pgdn)
        self.ui.key_bindings.add('c-v')(pgdn)
        self.ui.layout.focus(self.input_field)

    def output(self, st):
        self.output_field.buffer.cursor_position = len(self.output_field.buffer.text)
        self.output_field.buffer.insert_text(st.replace('\r', ''))

    def accept(self, buff):
        self.output(f'\n> {self.input_field.text}\n')
        self.parse(self.input_field.text)

    def parse(self, cmd):
        parts = cmd.split(maxsplit=1)
        if len(parts) == 0:
            return
        c = parts[0]
        if c == 'u':
            run_func = self.iii.upload
            global last_script
            if len(parts) == 1:
                if len(last_script) == 0:
                    self.output('  u <filename> to upload script')
                else:
                    run_func(last_script)
            elif len(parts) == 2 and os.path.isfile(parts[1]):
                last_script = parts[1]
                run_func(parts[1])
            else:
                self.iii.writeline(cmd)
        elif len(parts) == 1:
            if c == 'q':
                print('bye.')
                get_app().exit()
            elif c == 'p':
                self.iii.writeline('^^p')
            elif c == 'h':
                self.output(diii_help)
            else:
                self.iii.writeline(cmd)
        else:
            self.iii.writeline(cmd)

    def iii_event(self, line, event, args):
        if event == 'stream' or event == 'change':
            ch_str, val = args
            ch = int(ch_str)
            if ch >= 1 and ch <= 2:
                self.output_to_field(self.captures[ch - 1], f'\ninput[{ch}] = {val}\n')
        else:
            self.output(f'^^{event}({", ".join(args)})')

    # these come from:
    # https://github.com/prompt-toolkit/python-prompt-toolkit/blob/5c3d13eb849885bc4c1a2553ea6f81e6272f84c9/prompt_toolkit/key_binding/bindings/scroll.py#L147
    def pageup(self, event, field):
        w = field.window
        b = field.buffer
        if w and w.render_info:
            # Put cursor at the first visible line. (But make sure that the cursor
            # moves at least one line up.)
            line_index = max(
                0,
                min(w.render_info.first_visible_line(), b.document.cursor_position_row - 1),
            )

            b.cursor_position = b.document.translate_row_col_to_index(line_index, 0)
            b.cursor_position += b.document.get_start_of_line_position(
                after_whitespace=True
            )

            # Set the scroll offset. We can safely set it to zero; the Window will
            # make sure that it scrolls at least until the cursor becomes visible.
            w.vertical_scroll = 0

    def pagedown(self, event, field):
        w = field.window
        b = field.buffer
        if w and w.render_info:
            # Scroll down one page.
            line_index = max(w.render_info.last_visible_line(), w.vertical_scroll + 1)
            w.vertical_scroll = line_index

            b.cursor_position = b.document.translate_row_col_to_index(line_index, 0)
            b.cursor_position += b.document.get_start_of_line_position(
                after_whitespace=True
            )


class Diii:
    def __init__(self, iii, use_theme):
        self.iii = iii
        self.ui = DiiiUi(use_theme)
        self.repl = DiiiRepl(ui=self.ui, iii=iii)

        self.ui.add_page('repl', self.repl)
        self.ui.set_page('repl')

    async def foreground(self, script=None):
        if script is not None:
            if self.iii.is_connected == False:
                print('no iii device found. exiting.')
                return
            self.iii.execute(script)

        return await self.ui.app.run_async()

    async def background(self):
        await self.iii.read_forever()


log_config = {
    'version': 1,
    'formatters': {
        'detailed': {
            'class': 'logging.Formatter',
            'format': '%(asctime)s %(name)-15s %(levelname)-8s'
            '%(processName)-10s %(message)s'
        },
    },
    'handlers': {
        'file': {
            'class': 'logging.FileHandler',
            'filename': 'diii.log',
            'mode': 'w',
            'formatter': 'detailed',
        },
    },
    'loggers': {
        'diii.repl': {
            'handlers': ['file'],
        },
        'diii.iii': {
            'handlers': ['file'],
        },
    },
    'root': {
        'level': 'INFO',
        'handlers': [],
    },
}

def main(script=None, use_theme=True):
    try:
        logging.config.dictConfig(log_config)
    except ValueError:
        print('could not configure file logging (insufficient permissions?)')

    loop = asyncio.get_event_loop()
    use_asyncio_event_loop()
    with patch_stdout():
        with Deviceiii() as iii:
            shell = Diii(iii, use_theme)

            server = DiiiServer(shell.repl, 'localhost', 6666)
            iii.reconnect(err_event=True)
            background_task = asyncio.gather(
                shell.background(),
                server.listen(),
                return_exceptions=True,
            )
            loop.run_until_complete(shell.foreground())
            background_task.cancel()
