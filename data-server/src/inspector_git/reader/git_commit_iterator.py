# /src/inspector_git/gitclient/git_commit_iterator.py
from pathlib import Path
import threading
import sys
import logging
from typing import List, Iterable

from src.inspector_git.reader.git_log_pager import GitLogPager
from src.inspector_git.reader.iglog.iglog_constants import IGLogConstants
from src.inspector_git.utils.constants import TMP_FOLDER


LOG = logging.getLogger(__name__)


class GitCommitIterator:
    """
    Portare Python a clasei Kotlin GitCommitIterator.

    Comportament:
      - parcurge commits obținute paginat de la GitLogPager
      - cachează fiecare commit în fișiere numerotate în folderul TMP_FOLDER/commits
      - rulează caching-ul paginii următoare într-un thread background
      - sincronizează accesul între thread-ul cititor și cel de caching cu Condition
    """

    def __init__(self, git_client, page_size: int = 2000, page_number: int = 0):
        self.git_log_pager = GitLogPager(git_client, page_size)
        self.page_number = page_number

        self.lock = threading.Lock()
        self.condition = threading.Condition(self.lock)

        self.caching_in_progress: bool = False
        self.index: int = 0
        self.current_page: int = 0
        self.caching_index: int = 0

        # director temporar pentru commits
        self.temp_dir: Path = Path(TMP_FOLDER) / "commits"
        self.temp_dir.mkdir(parents=True, exist_ok=True)
        # curățăm orice fișiere vechi
        for f in self.temp_dir.iterdir():
            try:
                f.unlink()
            except Exception:
                LOG.debug("Couldn't delete temp file %s", f, exc_info=True)

    def has_next(self) -> bool:
        """
        True dacă mai sunt commits în pager, sau fișiere cache sau caching în desfășurare.
        """
        files = list(self.temp_dir.iterdir())
        return self.git_log_pager.has_next() or (len(files) > 0) or self.caching_in_progress

    def next(self) -> List[str]:
        """
        Returnează următoarea listă de linii care reprezintă un commit.
        Această metodă blochează dacă commit-ul nu a fost încă scris de thread-ul de caching.
        """
        with self.lock:
            self.index += 1

            files = list(self.temp_dir.iterdir())
            if (not self.caching_in_progress) and (len(files) == 0):
                # reset index to 1 when starting a fresh page (comportament Kotlin original)
                self.index = 1

            if self.index == 1:
                # pornim thread-ul de caching pentru pagina următoare
                def target():
                    try:
                        self._cache_next_page()
                    except Exception:
                        LOG.exception("Error while caching next page, exiting with code 2")
                        sys.exit(2)

                threading.Thread(target=target, daemon=True).start()
                self.current_page += 1

            LOG.debug("Requesting commit: %s on page: %s", self.index, self.current_page)

            # așteptăm până când commit-ul solicitat este scris (caching_index > index)
            # sau până când pagina curentă s-a schimbat (în acest caz reapelăm caching)
            while (self.index >= self.caching_index) or (self.current_page != getattr(self.git_log_pager, "counter", self.current_page)):
                LOG.debug("Waiting for commit: %s of page: %s to be cached", self.index, self.current_page)
                self.condition.wait()

            LOG.debug("Reading commit: %s of page: %s", self.index, self.current_page)
            file_path = self.temp_dir / str(self.index)
            try:
                with file_path.open("r", encoding="utf-8") as rf:
                    lines = [line.rstrip("\n") for line in rf.readlines()]
            finally:
                # încercăm să ștergem fișierul; dacă nu putem, nu opri execuția
                try:
                    file_path.unlink()
                except Exception:
                    LOG.debug("Could not delete commit cache file %s", file_path, exc_info=True)

            return lines

    def _cache_next_page(self):
        """
        Extrage o pagină (sau pagina setată la construire) din GitLogPager și scrie commit-urile
        în fișiere numerotate în temp_dir. Operația rulează într-un thread separat.
        """
        with self.lock:
            self.caching_in_progress = True

        if self.page_number == 0:
            page_stream = self.git_log_pager.next()
        else:
            page_stream = self.git_log_pager.page(self.page_number)

        # Normalize stream -> iterable of lines
        lines_iter = self._stream_to_lines(page_stream)

        # Respectăm logica Kotlin: la întâlnirea unei linii care începe cu commitIdPrefix,
        # apelăm cache_commit(currentCommitLines) și apoi reinicializăm lista curentă.
        self.caching_index = 0
        current_commit_lines: List[str] = []

        for line in lines_iter:
            if line.startswith(IGLogConstants.commit_id_prefix):
                # întâlnire delimitator commit -> scriem commit-ul colectat anterior
                self._cache_commit(current_commit_lines)
                current_commit_lines = []
            current_commit_lines.append(line)

        # scriem ultimul commit colectat (dacă există)
        self._cache_commit(current_commit_lines)

        with self.lock:
            self.caching_in_progress = False

    def _cache_commit(self, commit_lines: List[str]):
        """
        Scrie un commit (listă de linii) în fișierul numerotat corespunzător (dacă caching_index != 0).
        Semnalează condiția dacă cititorul așteaptă un index care a fost deja scris.
        """
        with self.lock:
            if self.caching_index != 0:
                LOG.debug("Caching commit: %s of page: %s", self.caching_index, getattr(self.git_log_pager, "counter", "?"))
                file_path = self.temp_dir / str(self.caching_index)
                try:
                    with file_path.open("w", encoding="utf-8") as wf:
                        for l in commit_lines:
                            wf.write(f"{l}\n")
                except Exception:
                    LOG.exception("Failed to write commit cache file %s", file_path)

                # dacă cititorul așteaptă acest index sau l-a depășit, semnalăm
                if self.index <= self.caching_index:
                    try:
                        self.condition.notify()
                    except RuntimeError:
                        # condition might be used without acquiring in rare races; safe to ignore
                        pass
            # incrementăm indexul de caching (comportament Kotlin: se incrementează la sfârșit)
            self.caching_index += 1

    @staticmethod
    def _stream_to_lines(stream) -> Iterable[str]:
        """
        Normalizează mai multe tipuri de "stream" în iterator de linii (fără newline).
        Acceptă:
         - obiecte file-like (au .read() sau .readline())
         - iterable de linii
         - string (va fi splitlines)
        """
        # file-like cu read()
        try:
            if hasattr(stream, "read") and not isinstance(stream, (str, bytes)):
                text = stream.read()
                if text is None:
                    return []
                return (line for line in text.splitlines())
        except Exception:
            LOG.debug("stream.read() failed, falling back to iteration", exc_info=True)

        # iterable (generator, list de linii etc.)
        if isinstance(stream, (list, tuple)) or hasattr(stream, "__iter__"):
            # asigurăm că fiecare element e str și eliminăm newline
            return (str(s).rstrip("\n") for s in stream)

        # string / bytes
        if isinstance(stream, bytes):
            return (line for line in stream.decode("utf-8", errors="replace").splitlines())
        if isinstance(stream, str):
            return (line for line in stream.splitlines())

        # fallback: iterator gol
        return iter(())

    # Python iterator protocol:
    def __iter__(self):
        return self

    def __next__(self):
        if self.has_next():
            return self.next()
        raise StopIteration