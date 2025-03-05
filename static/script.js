// 답변 기록 저장 함수
async function saveAnswer(userId, isCorrect, unit) {
    try {
        const response = await fetch('/admin/save-answer', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({
                user_id: userId,
                is_correct: isCorrect,
                unit: unit
            })
        });
        
        if (!response.ok) {
            throw new Error('답변 저장 실패');
        }
        
        const result = await response.json();
        console.log('답변 저장 결과:', result);
        
    } catch (error) {
        console.error('답변 저장 중 오류:', error);
    }
}

// 통계 삭제 함수
async function deleteStats(userId) {
    if (!confirm('정말 이 사용자의 통계를 삭제하시겠습니까?')) {
        return;
    }
    
    try {
        const response = await fetch(`/admin/stats/delete/${userId}`, {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error('통계 삭제 실패');
        }
        
        const result = await response.json();
        if (result.success) {
            alert('통계가 삭제되었습니다.');
            location.reload();
        } else {
            alert('통계 삭제 중 오류가 발생했습니다.');
        }
        
    } catch (error) {
        console.error('통계 삭제 중 오류:', error);
        alert('통계 삭제 중 오류가 발생했습니다.');
    }
}

// 모든 통계 삭제 함수
async function deleteAllStats() {
    if (!confirm('정말 모든 사용자의 통계를 삭제하시겠습니까?')) {
        return;
    }
    
    try {
        const response = await fetch('/admin/stats/delete-all', {
            method: 'POST'
        });
        
        if (!response.ok) {
            throw new Error('전체 통계 삭제 실패');
        }
        
        const result = await response.json();
        if (result.success) {
            alert('모든 통계가 삭제되었습니다.');
            location.reload();
        } else {
            alert('통계 삭제 중 오류가 발생했습니다.');
        }
        
    } catch (error) {
        console.error('전체 통계 삭제 중 오류:', error);
        alert('통계 삭제 중 오류가 발생했습니다.');
    }
}

// 단원별 통계 업데이트
function updateUnitStats(studentId) {
    const url = new URL(window.location.href);
    if (studentId) {
        url.searchParams.set('student_id', studentId);
    } else {
        url.searchParams.delete('student_id');
    }
    window.location.href = url.toString();
}

// 통계 다운로드 함수 추가
function downloadStatistics() {
    window.location.href = '/api/admin/statistics/download';
}

// DOM 로드 시 이벤트 리스너 설정
document.addEventListener('DOMContentLoaded', function() {
    // 개별 통계 삭제
    document.querySelectorAll('.delete-stats').forEach(button => {
        button.addEventListener('click', function() {
            const userId = this.dataset.userId;
            if (confirm('이 학생의 모든 통계를 삭제하시겠습니까?')) {
                fetch(`/admin/stats/delete/${userId}`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                }).then(response => {
                    if (response.ok) {
                        location.reload();
                    } else {
                        alert('통계 삭제 중 오류가 발생했습니다.');
                    }
                }).catch(error => {
                    console.error('Error:', error);
                    alert('통계 삭제 중 오류가 발생했습니다.');
                });
            }
        });
    });

    // 전체 통계 삭제
    const deleteAllBtn = document.getElementById('deleteAllBtn');
    if (deleteAllBtn) {
        deleteAllBtn.addEventListener('click', function() {
            if (confirm('모든 학생의 통계를 삭제하시겠습니까?\n이 작업은 되돌릴 수 없습니다.')) {
                fetch('/admin/stats/delete-all', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                }).then(response => {
                    if (response.ok) {
                        location.reload();
                    } else {
                        alert('통계 삭제 중 오류가 발생했습니다.');
                    }
                }).catch(error => {
                    console.error('Error:', error);
                    alert('통계 삭제 중 오류가 발생했습니다.');
                });
            }
        });
    }
});
