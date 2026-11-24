"""One-shot private V89 candidate build. No public receipts, logs, or APKs."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

RUNNER = 'zhui9808-netizen/aoh3-public-build'
RUNNER_ID = 1417062648
PRIVATE = 'zhui9808-netizen/codex-test'
PRIVATE_ID = 1357139625
SOURCE = '238052634fca19a0b7279e541ed227970085045c'
TREE = '2170f7fe5184ade9d94089d1efe84be2450cf119'
PUBLIC_BASE = '3f4be330c3f20a57552221f04dbae88aef4961e6'
BRANCH = 'candidate-v89-2380526-private-pair'
WORKFLOW = '.github/workflows/candidate-v89-private-pair.yml'
ENTRY = '.github/candidate_v89_launcher.py'
REF = 'refs/heads/' + BRANCH

def require(ok, detail):
    if not ok:
        raise RuntimeError(detail)

def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE, timeout=60)

def digest(data):
    return hashlib.sha256(data).hexdigest()

def verified_checkout(workspace, launcher_commit, *, base=PUBLIC_BASE):
    require(git(workspace, 'rev-parse', 'HEAD').decode().strip() == launcher_commit and
            not git(workspace, 'status', '--porcelain', '--untracked-files=normal').strip(),
            'Launcher checkout identity differs')
    require(git(workspace, 'show', '-s', '--format=%P', launcher_commit).decode().strip() == base,
            'Candidate must be one commit atop reviewed public base')
    changed = git(workspace, 'diff-tree', '--no-commit-id', '--name-status', '-r', base, launcher_commit).decode().splitlines()
    require(sorted(changed) == sorted(['A\t' + WORKFLOW, 'A\t' + ENTRY]),
            'Candidate must add only its isolated workflow and launcher')
    hashes = {}
    for entry in (WORKFLOW, ENTRY):
        path = workspace / entry
        require(path.is_file() and not path.is_symlink() and path.resolve() == path,
                'Launcher path differs')
        raw = path.read_bytes()
        require(raw == git(workspace, 'show', launcher_commit + ':' + entry),
                'Committed launcher bytes differ')
        hashes[entry] = digest(raw)
    return hashes

def verify_environment(env):
    expected = {'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                'RUNNER_OS': 'Linux', 'GITHUB_REPOSITORY': RUNNER,
                'GITHUB_REPOSITORY_ID': str(RUNNER_ID), 'GITHUB_REF': REF,
                'GITHUB_EVENT_NAME': 'push', 'GITHUB_RUN_ATTEMPT': '1',
                'GITHUB_WORKFLOW_REF': RUNNER + '/' + WORKFLOW + '@' + REF,
                'AOH_CI_SOURCE': SOURCE}
    require(all(env.get(k) == v for k, v in expected.items()), 'One-shot candidate runner identity differs')
    require('AOH_SIGN_PASSWORD' not in env and env.get('RUNNER_DEBUG') != '1'
            and env.get('ACTIONS_STEP_DEBUG', '').lower() != 'true', 'Unsafe candidate environment')
    for key, pattern in (('GITHUB_SHA', '[0-9a-f]{40}'), ('GITHUB_RUN_ID', '[1-9][0-9]{0,19}'),
                         ('AOH_CANDIDATE_TOOL_BLOB', '[0-9a-f]{40}'),
                         ('AOH_CANDIDATE_TOOL_SHA256', '[0-9a-f]{64}')):
        value = env.get(key, '')
        require(re.fullmatch(pattern, value) and set(value) != {'0'}, 'Exact candidate pin missing: ' + key)
    require(env['GITHUB_SHA'] == env.get('GITHUB_WORKFLOW_SHA'), 'Launcher commit differs')
    event = json.loads(Path(env['GITHUB_EVENT_PATH']).read_text())
    first_transition = ((event.get('created') is True and event.get('before') == '0' * 40) or
                        (event.get('created') is False and event.get('before') == PUBLIC_BASE))
    require(first_transition and event.get('deleted') is False and event.get('ref') == REF
            and event.get('after') == env['GITHUB_SHA'] and event.get('repository', {}).get('id') == RUNNER_ID,
            'Candidate only admits its first transition from the reviewed base')

def verify_remote(ci, github, public, env):
    for client, repo, identity, visibility in ((github, PRIVATE, PRIVATE_ID, True),
                                                (public, RUNNER, RUNNER_ID, False)):
        row = client.api('repos/' + repo)
        require(row.get('id') == identity and row.get('full_name') == repo and row.get('private') is visibility,
                'Repository identity or privacy differs')
    run = public.api('repos/' + RUNNER + '/actions/runs/' + env['GITHUB_RUN_ID'] + '/attempts/1')
    require(run.get('id') == int(env['GITHUB_RUN_ID']) and run.get('run_attempt') == 1
            and run.get('event') == 'push' and run.get('head_sha') == env['GITHUB_SHA']
            and run.get('head_branch') == BRANCH and run.get('repository', {}).get('id') == RUNNER_ID,
            'GitHub run does not bind this launcher')
    wid = run.get('workflow_id')
    require(type(wid) is int and wid > 0, 'Workflow identity missing')
    flow = public.api('repos/' + RUNNER + '/actions/workflows/' + str(wid))
    require(flow.get('id') == wid and flow.get('path') == WORKFLOW, 'Candidate workflow path differs')

def checked_builder_blob(row, blob, sha256):
    require(row.get('sha') == blob and row.get('encoding') == 'base64'
            and type(row.get('content')) is str and type(row.get('size')) is int
            and 0 < row['size'] <= 128 * 1024, 'Private candidate builder blob metadata differs')
    data = base64.b64decode(''.join(row['content'].split()), validate=True)
    require(len(data) == row['size'] and digest(data) == sha256 and
            hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest() == blob,
            'Private candidate builder blob digest differs')
    return data

def main():
    env = dict(os.environ)
    verify_environment(env)
    workspace = Path(env['GITHUB_WORKSPACE']).resolve()
    require(Path(__file__).resolve() == workspace / ENTRY, 'Candidate launcher path differs')
    workflow_hashes = verified_checkout(workspace, env['GITHUB_SHA'])
    private_temp = Path(env['RUNNER_TEMP']).resolve() / 'aoh3-private'
    root = private_temp / 'source'
    require(root.is_dir() and not root.is_symlink() and root.resolve() == root
            and git(root, 'rev-parse', 'HEAD').decode().strip() == SOURCE
            and git(root, 'rev-parse', 'HEAD^{tree}').decode().strip() == TREE,
            'Private production checkout identity differs')
    sys.path.insert(0, str(root / 'tools'))
    import ci_release_pair as ci
    from public_runner_pair import PublicMetadata, isolate_private_environment, private_failure_notice
    github = ci.GitHub()
    public = PublicMetadata()
    credentials = (github.token, public.token)
    isolate_private_environment()
    safe_env = {k: env[k] for k in ('GITHUB_RUN_ID', 'GITHUB_RUN_ATTEMPT',
                'GITHUB_SHA', 'RUNNER_TEMP', 'GITHUB_REPOSITORY', 'GITHUB_EVENT_NAME')}
    provenance = None
    try:
        require(ci.guard_source(SOURCE) == TREE, 'Committed production input guard differs')
        verify_remote(ci, github, public, env)
        public.close()
        blob, sha256 = env['AOH_CANDIDATE_TOOL_BLOB'], env['AOH_CANDIDATE_TOOL_SHA256']
        endpoint = 'repos/' + PRIVATE + '/git/blobs/' + blob
        data = checked_builder_blob(github.api(endpoint), blob, sha256)
        entry = private_temp / 'ci_candidate_pair.py'
        with entry.open('xb') as stream:
            stream.write(data)
        provenance = {'schema': 'aoh3-limited-candidate-runner-v1', 'formalRelease': False,
            'sourceRepository': PRIVATE, 'sourceRepositoryId': PRIVATE_ID, 'sourceCommit': SOURCE,
            'sourceTree': TREE, 'sourcePrivate': True, 'runnerRepository': RUNNER,
            'runnerRepositoryId': RUNNER_ID, 'runnerPrivate': False, 'workflowPath': WORKFLOW,
            'workflowRef': RUNNER + '/' + WORKFLOW + '@' + REF, 'workflowCommit': env['GITHUB_SHA'],
            'workflowSha256': workflow_hashes[WORKFLOW], 'entrypointSha256': workflow_hashes[ENTRY],
            'builderGitBlob': blob, 'builderSha256': sha256, 'githubRunId': env['GITHUB_RUN_ID'],
            'githubRunAttempt': env['GITHUB_RUN_ATTEMPT'], 'event': 'push'}
        spec = importlib.util.spec_from_file_location('ci_candidate_pair', entry)
        builder = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = builder
        spec.loader.exec_module(builder)
        builder.run(ci, github, SOURCE, TREE, provenance)
    except (Exception, KeyboardInterrupt) as error:
        private_failure_notice(ci, github, SOURCE, provenance, error, credentials, environment=safe_env)
        raise
    finally:
        public.close()

if __name__ == '__main__':
    try:
        main()
    except (Exception, KeyboardInterrupt):
        # The wrapper redirects this tool's output to the ephemeral private folder.
        print('Private candidate build failed.', file=sys.stderr)
        raise SystemExit(1)
