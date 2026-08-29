from PySide6.QtWidgets import QDialog,QVBoxLayout,QPlainTextEdit,QDialogButtonBox,QPushButton,QApplication
class DiagnosticsDialog(QDialog):
    def __init__(self,text,parent=None):
        super().__init__(parent); self.setWindowTitle('LaserWatch Diagnostics'); self.resize(780,620)
        layout=QVBoxLayout(self); self.editor=QPlainTextEdit(); self.editor.setReadOnly(True); self.editor.setPlainText(text); layout.addWidget(self.editor)
        buttons=QDialogButtonBox(QDialogButtonBox.Close); copy_btn=QPushButton('Copy'); copy_btn.clicked.connect(self.copy_text); buttons.addButton(copy_btn,QDialogButtonBox.ActionRole); buttons.rejected.connect(self.reject); layout.addWidget(buttons)
    def copy_text(self): QApplication.clipboard().setText(self.editor.toPlainText())
