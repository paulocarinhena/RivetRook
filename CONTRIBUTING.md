# Contributing to RivetRook

Obrigado pelo interesse! Thanks for your interest!

---

## Português (PT-BR)

### Reportar bugs
Abra uma [issue](../../issues/new) com:

- sistema operacional e versão do Python
- ferramenta que estava instalando/atualizando
- mensagem de erro completa
- passos para reproduzir

### Propor novas ferramentas
A contribuição mais comum é adicionar uma entrada em `src/config.json` em `tools` ou `ides`.

Passos:

1. Faça um fork do repositório.
2. Edite `src/config.json` e adicione a nova ferramenta em `tools` ou `ides`.
3. Teste nas plataformas disponíveis (Windows/Linux/macOS).
4. Abra um Pull Request descrevendo o que adicionou e como testou.

### Estrutura de entrada (`config.json`)

```jsonc
"nome-da-ferramenta": {
  "author": "Empresa ou Autor",
  "description": "Descrição em português",
  "description_en": "Description in English",
  "needs_git": 0,
  "install": {
    "all": "npm install -g pacote",
    "windows": "winget install -e --id Vendor.Tool",
    "macos": "brew install ferramenta",
    "linux": {
      "debian": "apt-get install -y ferramenta",
      "fedora": "dnf install -y ferramenta",
      "arch": "pacman -Sy --noconfirm ferramenta",
      "default": "npm install -g pacote"
    }
  },
  "upgrade": { "all": "npm install -g pacote@latest" },
  "uninstall": { "all": "npm uninstall -g pacote" },
  "run": "ferramenta",
  "version_cmd": { "all": "ferramenta --version" },
  "version_regex": "([\\d]+\\.[\\d]+\\.[\\d]+)"
}
```

Campos importantes:

- `description` e `description_en`: mantenha os dois textos atualizados.
- `run`: obrigatório para detecção de instalação.
- `version_cmd`/`version_regex`: recomendados quando `run --version` não é confiável.
- `skip_version_probe`: use em apps GUI para evitar abrir a interface durante detecção.
- `configure`: use para fluxo de API key.

### Código

- mantenha compatibilidade com Python 3.8+
- adicione docstrings em funções públicas novas
- mantenha comentários/docstrings do código em inglês
- todo texto de UI deve vir de `_t(...)` com chaves em `i18n.pt-br` e `i18n.en` no `config.json`
- preserve suporte a Windows, Linux e macOS

Executar localmente:

```bash
cd src
python3 RivetRook.py
```

No Windows, também pode usar `src/Execute_RivetRook.bat`.

### Pull Request

- descreva o que mudou e por quê
- informe em quais plataformas testou
- se adicionou ferramenta nova, atualize a lista de ferramentas no `README.md`

---

## English

### Reporting bugs
Open an [issue](../../issues/new) including:

- operating system and Python version
- tool you were installing/upgrading
- full error output
- reproduction steps

### Proposing new tools
The most common contribution is adding a new entry to `src/config.json` under `tools` or `ides`.

Steps:

1. Fork the repository.
2. Edit `src/config.json` and add your tool under `tools` or `ides`.
3. Test on available platforms (Windows/Linux/macOS).
4. Open a Pull Request explaining what you added and how you tested it.

### Entry structure (`config.json`)

```jsonc
"tool-name": {
  "author": "Company or Author",
  "description": "Descrição em português",
  "description_en": "Description in English",
  "needs_git": 0,
  "install": {
    "all": "npm install -g package",
    "windows": "winget install -e --id Vendor.Tool",
    "macos": "brew install tool",
    "linux": {
      "debian": "apt-get install -y tool",
      "fedora": "dnf install -y tool",
      "arch": "pacman -Sy --noconfirm tool",
      "default": "npm install -g package"
    }
  },
  "upgrade": { "all": "npm install -g package@latest" },
  "uninstall": { "all": "npm uninstall -g package" },
  "run": "tool",
  "version_cmd": { "all": "tool --version" },
  "version_regex": "([\\d]+\\.[\\d]+\\.[\\d]+)"
}
```

Important fields:

- `description` and `description_en`: keep both updated.
- `run`: required for install detection.
- `version_cmd`/`version_regex`: recommended when `run --version` is unreliable.
- `skip_version_probe`: use for GUI apps to avoid launching the app during detection.
- `configure`: use for API key setup flow.

### Code changes

- keep Python 3.8+ compatibility
- add docstrings for new public functions
- keep source comments/docstrings in English
- all user-facing strings must come from `_t(...)` and be added to both `i18n.pt-br` and `i18n.en` in `config.json`
- preserve Windows, Linux, and macOS support

Run locally:

```bash
cd src
python3 RivetRook.py
```

On Windows, you can also use `src/Execute_RivetRook.bat`.

### Pull Request

- explain what changed and why
- include which platforms you tested
- if you added a new tool, update the supported tools list in `README.md`

---

Questions? Open a [discussion](../../discussions) or an [issue](../../issues).
