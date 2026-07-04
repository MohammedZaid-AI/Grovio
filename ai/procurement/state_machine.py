class InvalidStatusTransition(Exception):
    pass

class PurchaseOrderStateMachine:
    ALLOWED_TRANSITIONS = {
        'DRAFT': [
            'APPROVED',
            'REJECTED'          # Pre-approval rejection
        ],
        'APPROVED': [
            'ORDERED',
            'CANCELLED'         # Post-approval cancellation
        ],
        'ORDERED': [
            'SHIPPED',
            'CANCELLED'         # Post-order cancellation
        ],
        'SHIPPED': [
            'RECEIVED'
        ],
        'RECEIVED': [
            'INVENTORY_UPDATED'
        ],
        'INVENTORY_UPDATED': [
            'CLOSED'
        ],
        'REJECTED': [],          # Terminal state (pre-approval)
        'CANCELLED': [],         # Terminal state (post-approval/order)
        'CLOSED': []             # Terminal state (successful completion)
    }

    @classmethod
    def validate_transition(cls, current_status: str, target_status: str):
        current_status = current_status.upper()
        target_status = target_status.upper()
        
        if current_status not in cls.ALLOWED_TRANSITIONS:
            raise InvalidStatusTransition(f"Unknown status: {current_status}")
            
        if target_status not in cls.ALLOWED_TRANSITIONS:
            raise InvalidStatusTransition(f"Unknown status: {target_status}")
            
        allowed = cls.ALLOWED_TRANSITIONS[current_status]
        if target_status not in allowed:
            raise InvalidStatusTransition(
                f"Invalid transition from {current_status} to {target_status}. Allowed targets: {allowed}"
            )
