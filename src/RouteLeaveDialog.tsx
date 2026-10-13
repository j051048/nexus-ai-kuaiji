import { Modal } from './AppPrimitives';
export function RouteLeaveDialog({
  canDiscard,
  onStay,
  onLeave,
}: {
  canDiscard: boolean;
  onStay: () => void;
  onLeave: () => void;
}) {
  return (
    <Modal title="当前工作尚未结束" onClose={onStay}>
      <div className="help-content">
        <p>
          {canDiscard
            ? '有尚未保存的修改。返回当前页面继续处理，或明确放弃修改后离开。'
            : '当前操作正在进行，请返回当前页面，等待完成后再切换。'}
        </p>
      </div>
      <div className="modal-footer">
        <button className="button secondary" onClick={onStay}>
          继续当前工作
        </button>
        {canDiscard && (
          <button className="button primary" onClick={onLeave}>
            放弃修改并离开
          </button>
        )}
      </div>
    </Modal>
  );
}
