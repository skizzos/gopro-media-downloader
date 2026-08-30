#!/usr/bin/env python3
"""
Interfaccia a schermo intero (Textual) per gopro_media_downloader.py.

Non duplica la logica di download: importa gopro_media_downloader come
modulo e richiama le stesse funzioni usate dalla CLI (mode_test,
mode_test_download, mode_download, mode_download_by_filename). L'output
che quelle funzioni normalmente stampano a video viene catturato e
mostrato nel pannello di log; il progresso del download corrente viene
mostrato nella barra in alto tramite l'hook opzionale
core.set_progress_hook().

REQUISITI (in più rispetto allo script CLI):
    pip3 install textual --break-system-packages

USO:
    ./gopro_tui.py
"""

import contextlib
import io
import sys

import gopro_media_downloader as core

try:
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import Screen
    from textual.widgets import (
        Button,
        Checkbox,
        Footer,
        Header,
        Input,
        ProgressBar,
        RadioButton,
        RadioSet,
        RichLog,
        Select,
        Static,
        TabbedContent,
        TabPane,
    )
    from textual.worker import Worker, WorkerState
except ImportError:
    print("ERRORE: manca la libreria 'textual', serve solo per questa interfaccia grafica.")
    print("Installala con: pip3 install textual --break-system-packages")
    sys.exit(1)


class _LogWriter(io.TextIOBase):
    """file-like che inoltra ogni riga scritta a una callback (usata per
    catturare i print() delle funzioni core.mode_* dentro il RichLog)."""

    def __init__(self, callback):
        self._callback = callback
        self._buf = ""

    def write(self, s):
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self._callback(line)
        return len(s)

    def flush(self):
        pass


class LoginScreen(Screen):
    """Schermata iniziale: scelta browser + conferma login, prima di
    qualunque chiamata all'API (stesso vincolo della CLI)."""

    CSS = """
    LoginScreen {
        align: center middle;
    }
    #login_box {
        width: 60;
        height: auto;
        border: round $accent;
        padding: 1 2;
    }
    #login_box Static {
        margin-bottom: 1;
    }
    #login_status {
        color: $text-muted;
        margin-top: 1;
    }
    """

    def compose(self) -> ComposeResult:
        opzioni = [(label, browser_key) for browser_key, label, _nota in core.BROWSER_OPZIONI.values()]
        with Vertical(id="login_box"):
            yield Static(f"gopro_downloader TUI v{core.__version__}", id="titolo")
            yield Static("Da quale browser leggo i cookie di sessione GoPro?")
            yield Select(opzioni, value="brave", id="browser_select", allow_blank=False)
            yield Checkbox("Ho già fatto login su gopro.com in questo browser", id="login_ok")
            yield Button("Continua", id="continua", variant="primary", disabled=True)
            yield Static("", id="login_status")

    def on_checkbox_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#continua", Button).disabled = not event.value

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "continua":
            self.query_one("#continua", Button).disabled = True
            self.query_one("#login_status", Static).update("Verifico i cookie...")
            self.verifica_login()

    @property
    def browser_key(self):
        return self.query_one("#browser_select", Select).value

    def verifica_login(self) -> None:
        self.run_worker(self._verifica_login_worker, thread=True, exclusive=True)

    def _verifica_login_worker(self) -> None:
        browser_key = self.browser_key
        buf = []
        try:
            with contextlib.redirect_stdout(_LogWriter(buf.append)):
                cookies = core.estrai_cookie(browser_key)
                access_token = cookies.get("gp_access_token", "")
                user_id = cookies.get("gp_user_id", "")
        except SystemExit:
            self.app.call_from_thread(self._login_fallita, "\n".join(buf))
            return

        mancanti = [n for n, v in (("gp_access_token", access_token), ("gp_user_id", user_id)) if not v]
        if mancanti:
            msg = f"Cookie mancanti o vuoti: {', '.join(mancanti)}. Fai login su gopro.com dentro {browser_key} e riprova."
            self.app.call_from_thread(self._login_fallita, msg)
            return

        core.session.cookies.set("gp_access_token", access_token, domain=".gopro.com")
        core.session.cookies.set("gp_user_id", user_id, domain=".gopro.com")
        self.app.call_from_thread(self._login_ok)

    def _login_fallita(self, msg) -> None:
        self.query_one("#login_status", Static).update(f"[red]{msg}[/red]")
        self.query_one("#continua", Button).disabled = False

    def _login_ok(self) -> None:
        self.app.push_screen(MainScreen())


class MainScreen(Screen):
    """Schermata principale: tab per ogni azione, log condiviso e barra di
    progresso del download in corso."""

    CSS = """
    #progress_row {
        height: 3;
        padding: 0 1;
    }
    #progress_label {
        width: auto;
        padding-right: 1;
    }
    RichLog {
        border: round $accent;
        height: 1fr;
    }
    .form {
        padding: 1 2;
        height: auto;
    }
    .form Input, .form RadioSet, .form Checkbox, .form Button {
        margin-bottom: 1;
    }
    """

    BINDINGS = [("q", "quit_app", "Esci")]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="progress_row"):
            yield Static("Pronto.", id="progress_label")
            yield ProgressBar(id="progress_bar", show_eta=False)
        with TabbedContent():
            with TabPane("Giornata", id="tab_giorno"):
                with Vertical(classes="form"):
                    yield Input(placeholder="Data (AAAAMMGG o AAAA-MM-GG)", id="giorno_data")
                    with RadioSet(id="giorno_tipo"):
                        yield RadioButton("Solo foto", id="giorno_foto")
                        yield RadioButton("Solo video", id="giorno_video")
                        yield RadioButton("Entrambi", id="giorno_tutti", value=True)
                    yield Button("Scarica", id="giorno_go", variant="primary")
            with TabPane("Range di date", id="tab_range"):
                with Vertical(classes="form"):
                    yield Input(placeholder="Data inizio (AAAAMMGG o AAAA-MM-GG)", id="range_da")
                    yield Input(placeholder="Data fine (AAAAMMGG o AAAA-MM-GG)", id="range_a")
                    with RadioSet(id="range_tipo"):
                        yield RadioButton("Solo foto", id="range_foto")
                        yield RadioButton("Solo video", id="range_video")
                        yield RadioButton("Entrambi", id="range_tutti", value=True)
                    yield Button("Scarica", id="range_go", variant="primary")
            with TabPane("Cerca file", id="tab_file"):
                with Vertical(classes="form"):
                    yield Input(placeholder="Nome (o parte) del file", id="file_nome")
                    yield Checkbox("Cerca in tutta la libreria (più lento)", id="file_tutti")
                    yield Button("Cerca e scarica", id="file_go", variant="primary")
            with TabPane("Test", id="tab_test"):
                with Vertical(classes="form"):
                    yield Static("Verifica che la prima pagina di /media/search risponda come atteso.")
                    yield Button("Esegui test", id="test_go", variant="primary")
            with TabPane("Test-download", id="tab_testdl"):
                with Vertical(classes="form"):
                    yield Input(placeholder="media_id", id="testdl_id")
                    yield Button("Stampa risposta grezza", id="testdl_go", variant="primary")
        yield RichLog(id="log", markup=True, wrap=True)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#progress_bar", ProgressBar).display = False
        core.set_progress_hook(self._on_progress)
        self.log_widget = self.query_one("#log", RichLog)
        self.log_widget.write("Pronto. Scegli un'azione dalle tab qui sopra.")

    # ---- log / progresso ----

    def _log_line(self, line: str) -> None:
        self.log_widget.write(line)

    def _on_progress(self, dest_path, downloaded, total) -> None:
        self.app.call_from_thread(self._update_progress, dest_path, downloaded, total)

    def _update_progress(self, dest_path, downloaded, total) -> None:
        bar = self.query_one("#progress_bar", ProgressBar)
        label = self.query_one("#progress_label", Static)
        nome = dest_path.rsplit("/", 1)[-1]
        if total:
            bar.display = True
            bar.update(total=total, progress=downloaded)
            pct = int(downloaded * 100 / total)
            label.update(f"{nome}: {pct}%")
        else:
            bar.display = False
            label.update(f"{nome}: scaricato {downloaded} byte")

    def _reset_progress(self) -> None:
        self.query_one("#progress_bar", ProgressBar).display = False
        self.query_one("#progress_label", Static).update("Pronto.")

    # ---- azioni ----

    def action_quit_app(self) -> None:
        self.app.exit()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "giorno_go":
            self._run_giorno()
        elif bid == "range_go":
            self._run_range()
        elif bid == "file_go":
            self._run_file()
        elif bid == "test_go":
            self._run_semplice(core.mode_test, "test")
        elif bid == "testdl_go":
            media_id = self.query_one("#testdl_id", Input).value.strip()
            if not media_id:
                self.log_widget.write("[yellow]Inserisci un media_id.[/yellow]")
                return
            self._run_semplice(lambda: core.mode_test_download(media_id), "test-download")

    def _tipo_scelto(self, radioset_id, mapping) -> str:
        rs = self.query_one(f"#{radioset_id}", RadioSet)
        if rs.pressed_button is not None:
            return mapping.get(rs.pressed_button.id, "tutti")
        return "tutti"

    def _run_giorno(self) -> None:
        giorno = self.query_one("#giorno_data", Input).value.strip()
        if not giorno:
            self.log_widget.write("[yellow]Inserisci una data.[/yellow]")
            return
        tipo = self._tipo_scelto("giorno_tipo", {"giorno_foto": "foto", "giorno_video": "video", "giorno_tutti": "tutti"})
        self._avvia_download(giorno, giorno, tipo)

    def _run_range(self) -> None:
        da = self.query_one("#range_da", Input).value.strip()
        a = self.query_one("#range_a", Input).value.strip()
        if not da or not a:
            self.log_widget.write("[yellow]Inserisci data inizio e fine.[/yellow]")
            return
        tipo = self._tipo_scelto("range_tipo", {"range_foto": "foto", "range_video": "video", "range_tutti": "tutti"})
        self._avvia_download(da, a, tipo)

    def _avvia_download(self, da_raw, a_raw, tipo) -> None:
        try:
            with contextlib.redirect_stdout(_LogWriter(self._log_line)):
                core.DATE_FROM = core.parse_cli_date(da_raw)
                core.DATE_TO = core.parse_cli_date(a_raw)
        except SystemExit:
            return
        if core.DATE_FROM > core.DATE_TO:
            core.DATE_FROM, core.DATE_TO = core.DATE_TO, core.DATE_FROM
        self.log_widget.write(f"[bold]Range: {core.DATE_FROM} -> {core.DATE_TO} ({tipo})[/bold]")
        self.run_worker(lambda: self._worker_wrap(lambda: core.mode_download(media_type=tipo)), thread=True, exclusive=True)

    def _run_file(self) -> None:
        nome = self.query_one("#file_nome", Input).value.strip()
        if not nome:
            self.log_widget.write("[yellow]Inserisci un nome file.[/yellow]")
            return
        scan_completo = self.query_one("#file_tutti", Checkbox).value
        self.run_worker(
            lambda: self._worker_wrap(lambda: core.mode_download_by_filename(nome, scan_completo=scan_completo)),
            thread=True,
            exclusive=True,
        )

    def _run_semplice(self, fn, etichetta) -> None:
        self.run_worker(lambda: self._worker_wrap(fn), thread=True, exclusive=True)

    def _worker_wrap(self, fn) -> None:
        """Esegue fn() catturando tutto quello che stampa e riversandolo nel
        log, riga per riga, in tempo reale (nessun buffering fino alla fine)."""
        def emetti(line):
            self.app.call_from_thread(self._log_line, line)

        try:
            with contextlib.redirect_stdout(_LogWriter(emetti)):
                fn()
        except SystemExit:
            self.app.call_from_thread(self._log_line, "[red]Operazione interrotta (vedi messaggio sopra).[/red]")
        except Exception as e:
            self.app.call_from_thread(self._log_line, f"[red]Errore inatteso: {e}[/red]")
        finally:
            self.app.call_from_thread(self._reset_progress)


class GoProTUI(App):
    TITLE = "GoPro Media Downloader"

    def on_mount(self) -> None:
        self.push_screen(LoginScreen())


def main():
    GoProTUI().run()


if __name__ == "__main__":
    main()
