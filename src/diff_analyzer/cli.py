"""Command-line entry point: parses arguments, prints the result, maps errors to exit codes."""

import argparse
import sys
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from diff_analyzer.config import Settings, load_settings
from diff_analyzer.diff_source import Commit, GitMode, Range, Staged, WorkingTree
from diff_analyzer.errors import DiffAnalyzerError
from diff_analyzer.llm_client import LLMClient, OpenAIClient
from diff_analyzer.prompt import DefaultPrompt, InlinePrompt, PromptFile, PromptSource
from diff_analyzer.service import AnalysisRequest, analyze

ClientFactory = Callable[[Settings], LLMClient]


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.exit(2, f"Erro: {message}\n")


def main(argv: list[str] | None = None, *, client_factory: ClientFactory = OpenAIClient) -> int:
    """Run the CLI with `argv` and return the exit code."""
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        request = AnalysisRequest(args.source, _git_mode(args), _prompt_source(args))
        client = client_factory(load_settings())
        response = analyze(request, client)
    except DiffAnalyzerError as exc:
        sys.stderr.write(f"Erro: {exc}\n")
        return exc.exit_code
    text = response.text
    sys.stdout.write(text if text.endswith("\n") else f"{text}\n")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="diff-analyzer",
        description="Envia um diff para um LLM e imprime a análise.",
        epilog="Configuração do LLM vem do arquivo .env.",
        add_help=False,
    )
    source = parser.add_argument_group("origem do diff")
    source.add_argument(
        "source",
        metavar="SOURCE",
        help="arquivo .diff ou diretório de um repositório git",
    )

    git = parser.add_argument_group(
        "modo git", "Para quando SOURCE é um repositório. Sem nenhum: git diff HEAD."
    )
    modes = git.add_mutually_exclusive_group()
    modes.add_argument(
        "--staged", action="store_true", help="só as mudanças preparadas (git diff --cached)"
    )
    modes.add_argument(
        "--commit", metavar="REF", help="as mudanças de um commit (git show --format= REF)"
    )
    modes.add_argument("--range", metavar="A..B", help="as mudanças entre A e B (git diff A..B)")

    prompt = parser.add_argument_group("prompt", "Sem nenhum, usa o prompt padrão do pacote.")
    prompts = prompt.add_mutually_exclusive_group()
    prompts.add_argument("-p", "--prompt", metavar="TEXTO", help="instruções passadas na linha")
    prompts.add_argument(
        "--prompt-file", metavar="ARQUIVO", type=Path, help="arquivo markdown com as instruções"
    )

    other = parser.add_argument_group("outras opções")
    other.add_argument("-h", "--help", action="help", help="mostra esta ajuda e sai")
    return parser


def _git_mode(args: argparse.Namespace) -> GitMode:
    if args.staged:
        return Staged()
    if args.commit is not None:
        return Commit(args.commit)
    if args.range is not None:
        return Range(args.range)
    return WorkingTree()


def _prompt_source(args: argparse.Namespace) -> PromptSource:
    if args.prompt is not None:
        return InlinePrompt(args.prompt)
    if args.prompt_file is not None:
        return PromptFile(args.prompt_file)
    return DefaultPrompt()
