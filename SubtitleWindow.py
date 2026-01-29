import sys
import queue
import threading
import time
import tkinter as tk

# ==================== 字幕窗口管理器 ====================
class SubtitleWindow:
    """半透明字幕窗口管理器"""
    
    def __init__(self):
        self.root = None
        self.label = None
        self.is_showing = False
        self.lock = threading.Lock()
        self.pending_operation = None  # 即将执行的操作
        self.current_operation = None  # 当前正在进行的操作
        self.completed_operation = None  # 最近完成的操作
        self._thread = None
        self._update_queue = queue.Queue()
        self._should_close = False
        self._update_counter = 0
        
    def _create_window(self):
        """创建半透明字幕窗口"""
        try:
            self.root = tk.Tk()
            self.root.title("操作状态")
            self.root.attributes('-topmost', True)  # 置顶
            self.root.attributes('-alpha', 0.7)  # 半透明
            self.root.overrideredirect(True)  # 无边框
            
            # 获取屏幕尺寸
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            # 窗口尺寸
            window_width = 400
            window_height = 80
            
            # 位置：屏幕底部偏上居中（距底约 150 像素）
            x = (screen_width - window_width) // 2
            y = screen_height - window_height - 150
            
            self.root.geometry(f"{window_width}x{window_height}+{x}+{y}")
            
            # 创建半透明背景框架
            frame = tk.Frame(self.root, bg='black')
            frame.pack(fill=tk.BOTH, expand=True)
            
            # 创建标签显示文本
            self.label = tk.Label(
                frame,
                text="",
                bg='black',
                fg='white',
                font=('Microsoft YaHei', 16, 'bold'),
                wraplength=380,
                justify=tk.CENTER
            )
            self.label.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
            
            # 处理窗口关闭事件
            self.root.protocol("WM_DELETE_WINDOW", self._on_close)
            
        except Exception as e:
            print(f"[字幕窗口错误] 创建窗口失败: {str(e)}")
            sys.stdout.flush()
    
    def _on_close(self):
        """窗口关闭事件处理"""
        pass  # 不允许手动关闭，只能通过hide方法关闭
    
    def _update_text(self):
        """更新字幕文本"""
        if not self.label:
            return
        
        text = ""
        if self.pending_operation:
            text = f"即将执行: {self.pending_operation}"
        elif self.current_operation:
            text = f"进行中: {self.current_operation}"
        elif self.completed_operation:
            text = f"已完成: {self.completed_operation}"
        # 如果都没有，text保持为空，只显示半透明背景
        
        self.label.config(text=text)
    
    def _window_thread(self):
        """窗口线程主循环"""
        try:
            self._create_window()
            if not self.root:
                return
            
            # 初始更新
            self._update_text()
            
            # 处理更新队列
            self._should_close = False
            
            def check_queue():
                if self._should_close:
                    if self.root:
                        self.root.quit()
                    return
                
                try:
                    while True:
                        try:
                            update_type = self._update_queue.get_nowait()
                            if update_type == "update":
                                self._update_text()
                            elif update_type == "close":
                                self._should_close = True
                                if self.root:
                                    self.root.quit()
                                return
                        except queue.Empty:
                            break
                except Exception:
                    pass
                
                if self.root and not self._should_close:
                    self.root.after(100, check_queue)  # 每100ms检查一次
            
            check_queue()
            
            # 运行主循环
            self.root.mainloop()
            
        except Exception as e:
            print(f"[字幕窗口错误] 窗口线程异常: {str(e)}")
            sys.stdout.flush()
        finally:
            # 注意：此处不能加 with self.lock，否则会与 hide_with_delay 的
            # delayed_close 线程死锁（该线程持锁并 join 本线程，本线程又需持锁）
            if self.root:
                try:
                    self.root.destroy()
                except Exception:
                    pass
            self.root = None
            self.label = None
            self.is_showing = False
    
    def _mark_update(self):
        """记录字幕更新，避免延迟关闭"""
        self._update_counter += 1

    def show(self):
        """显示字幕窗口"""
        with self.lock:
            if self.is_showing:
                return
            
            self.is_showing = True
            self._thread = threading.Thread(target=self._window_thread, daemon=True)
            self._thread.start()
            
            # 等待窗口创建完成
            for _ in range(50):  # 最多等待5秒
                time.sleep(0.1)
                if self.root:
                    break
    
    def hide(self):
        """隐藏字幕窗口"""
        self.hide_with_delay(0.0)

    def hide_with_delay(self, delay_seconds: float):
        """延迟隐藏字幕窗口"""
        with self.lock:
            if not self.is_showing:
                return
            
            if delay_seconds <= 0:
                self._close_window_locked()
                return
            
            update_snapshot = self._update_counter
            def delayed_close():
                time.sleep(delay_seconds)
                with self.lock:
                    if not self.is_showing:
                        return
                    if self._update_counter != update_snapshot:
                        return
                    self._close_window_locked()
            threading.Thread(target=delayed_close, daemon=True).start()

    def _close_window_locked(self):
        """关闭窗口并清理状态（需持有lock）"""
        self.is_showing = False
        self.pending_operation = None
        self.current_operation = None
        self.completed_operation = None
        
        if self.root:
            try:
                self._update_queue.put("close")
                # 等待线程结束（最多等待2秒）
                if self._thread and self._thread.is_alive():
                    self._thread.join(timeout=2.0)
            except Exception as e:
                print(f"[字幕窗口错误] 关闭窗口失败: {str(e)}")
                sys.stdout.flush()
        
        # 清理状态
        self.root = None
        self.label = None
        self._thread = None
    
    def set_current_operation(self, operation: str):
        """设置当前正在进行的操作"""
        with self.lock:
            self.pending_operation = None
            self.current_operation = operation
            self.completed_operation = None  # 清除已完成的操作
            self._mark_update()
            if self.is_showing and self.root:
                self._update_queue.put("update")
    
    def set_completed_operation(self, operation: str):
        """设置已完成的操作"""
        with self.lock:
            self.pending_operation = None
            self.current_operation = None  # 清除进行中的操作
            self.completed_operation = operation
            self._mark_update()
            if self.is_showing and self.root:
                self._update_queue.put("update")

    def set_pending_operation(self, operation: str):
        """设置即将执行的操作"""
        with self.lock:
            self.pending_operation = operation
            self.current_operation = None
            self.completed_operation = None
            self._mark_update()
            if self.is_showing and self.root:
                self._update_queue.put("update")

    def clear_operations(self):
        """清除所有操作状态（只显示背景）"""
        with self.lock:
            self.pending_operation = None
            self.current_operation = None
            self.completed_operation = None
            self._mark_update()
            if self.is_showing and self.root:
                self._update_queue.put("update")

# 全局字幕窗口实例
subtitle_window = SubtitleWindow()
# ===================================================
