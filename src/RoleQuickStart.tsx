import type { RoleHomeGuide } from './workspace-role-home';
import './workspace-help.css';

/** Static guidance only: no model, task mutation, tenant facts or navigation. */
export function RoleQuickStart({ guide }: { guide: RoleHomeGuide }) {
  return (
    <dl className="role-quick-start" aria-label="当前岗位入门说明">
      <div>
        <dt>先做什么</dt>
        <dd>{guide.firstStep}</dd>
      </div>
      <div>
        <dt>做完交给谁</dt>
        <dd>{guide.handoff}</dd>
      </div>
      <div>
        <dt>举个例子</dt>
        <dd>{guide.example}</dd>
      </div>
    </dl>
  );
}
